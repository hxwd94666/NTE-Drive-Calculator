# 为旧 UI 与不可变 Context 提供唯一的内存态配装算法入口。
"""Shared in-memory allocation kernel for legacy and frozen-context callers.

The kernel deliberately owns no scoring formula, puzzle generation or strategy
implementation.  It prepares the established ``ScoringEngine`` and
``DispatcherEngine`` with one explicit request contract, so the old UI and the
v5 Context adapter cannot drift into separate recommendation algorithms.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from src.i18n import display_term, tr
from src.models.equipment import Drive, Tape
from src.domain.suit_identity import tape_matches_suit_target
from src.optimizer.dispatcher import DispatcherEngine
from src.optimizer.scoring import ScoringEngine
from src.utils.logger import logger


@dataclass(frozen=True, slots=True)
class AllocationPropertyLimit:
    """A legacy scoring-label bound expressed in legacy inventory units."""

    stat_name: str
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True, slots=True)
class AllocationKernelRequest:
    """All prepared in-memory inputs for the one allocation implementation."""

    inventory: tuple[Drive | Tape, ...]
    roles_db: Mapping[str, Mapping]
    sets_db: Mapping[str, Mapping]
    shapes_db: Mapping[str, object]
    blueprints_db: Mapping[str, list[dict]]
    role_order: tuple[str, ...]
    strategy: str
    module_set_targets: Mapping[str, str]
    set_effect_modes: Mapping[str, str]
    core_main_filters: Mapping[str, tuple[str, ...]]
    core_set_targets: Mapping[str, str | None]
    stat_priority_configs: Mapping[str, Mapping]
    property_limits: Mapping[str, tuple[AllocationPropertyLimit, ...]]
    priority_groups: tuple[tuple[str, ...], ...] = ()
    crit_rate_caps: Mapping[str, float] = None
    # Retained only for legacy caller compatibility.  A missing core/card can
    # never invalidate a complete drive blueprint.
    allow_missing_core: bool = False
    drive_screen_limit: int = 15
    tape_screen_limit: int = 3


def estimate_candidate_pool_limits(
    blueprints_db: Mapping[str, Sequence[Mapping]],
    role_order: Sequence[str],
    priority_groups: Sequence[Sequence[str]] = (),
) -> tuple[int, int]:
    """Return the legacy-safe drive and core screen limits for one request.

    A unified allocation needs enough same-shape candidates for every selected
    role, not only the best fifteen items for an individual role.  The request
    already has generated blueprints, making this computation independent of
    both UI configuration and DAO access.
    """

    shape_demands: dict[str, int] = {}
    for role_name, blueprints in blueprints_db.items():
        role_max_demands: dict[str, int] = {}
        for blueprint in blueprints or ():
            counts: dict[str, int] = {}
            for shape_id in (*tuple(blueprint.get("set_pieces") or ()), *tuple(blueprint.get("extra_pieces") or ())):
                counts[str(shape_id)] = counts.get(str(shape_id), 0) + 1
            for shape_id, count in counts.items():
                role_max_demands[shape_id] = max(role_max_demands.get(shape_id, 0), count)
        for shape_id, count in role_max_demands.items():
            shape_demands[shape_id] = shape_demands.get(shape_id, 0) + count

    selected = set(role_order)
    group_size = max(
        (len([role for role in group if role in selected]) for group in priority_groups or ()),
        default=1,
    )
    drive_limit = max(15, max(shape_demands.values(), default=0) + 5, group_size * 10)
    # Strict-priority roles can receive separate per-role lists containing
    # the same physical core UIDs. Retain at least one distinct candidate per
    # selected role so cross-role uniqueness does not strand later roles.
    tape_limit = max(6, len(role_order), group_size * 4)
    return drive_limit, tape_limit


class AllocationKernel:
    """Invoke the existing scorer and dispatcher with an explicit contract."""

    def __init__(self, scoring_engine: ScoringEngine) -> None:
        self.scoring_engine = scoring_engine

    def execute(self, request: AllocationKernelRequest) -> dict:
        """Return the historic AllocationResult while enforcing complete plans.

        Property limits are deliberately applied by rerunning the same kernel
        with selected candidates excluded.  This is an extension of the input
        contract, not a second score or ranking formula.
        """

        has_limits = any(request.property_limits.get(role) for role in request.role_order)
        use_full_global_candidates = False
        initial = self._execute_once(request, frozenset())
        initial_invalid = self._invalid_roles(request, initial)
        if initial_invalid and request.strategy == "global_optimal":
            # 全局最优只接受完整整队方案。常规 Top-K 并集无解时，不能
            # 降级成局部补配；改用完整已评分背包重跑同一套全局匹配即可。
            logger.debug(
                "全局最优在常规候选范围内未找到完整全队方案；"
                "正在扩展至完整背包候选后整队重算。"
            )
            use_full_global_candidates = True
            initial = self._execute_once(
                request, frozenset(), use_full_drive_candidates=True,
            )
            initial_invalid = self._invalid_roles(request, initial)
        if not has_limits:
            if not initial_invalid:
                return initial
            self._apply_invalid_diagnostics(
                request, initial, initial_invalid,
                full_global_candidates=use_full_global_candidates,
            )
            return initial

        pending = [frozenset()]
        seen: set[frozenset[str]] = set()
        best: dict | None = None
        best_score = float("-inf")
        while pending and len(seen) < 256:
            excluded = pending.pop(0)
            if excluded in seen:
                continue
            seen.add(excluded)
            result = self._execute_once(
                request, excluded,
                use_full_drive_candidates=use_full_global_candidates,
            )
            invalid_roles = self._invalid_roles(request, result)
            if not invalid_roles:
                # Preserve the historic first-result tie behaviour.
                score = sum(float((result.get(role) or {}).get("score", 0.0)) for role in request.role_order)
                if best is None or score > best_score:
                    best, best_score = result, score
                if not has_limits:
                    return result
                continue
            for role in invalid_roles:
                for item in self._plan_items(result.get(role) or {}):
                    pending.append(excluded | {item.uid})
        if best is not None:
            return best

        failed = self._execute_once(
            request, frozenset(),
            use_full_drive_candidates=use_full_global_candidates,
        )
        self._apply_invalid_diagnostics(
            request, failed, self._invalid_roles(request, failed),
            full_global_candidates=use_full_global_candidates,
        )
        return failed

    def _execute_once(
        self,
        request: AllocationKernelRequest,
        excluded_uids: frozenset[str],
        *,
        use_full_drive_candidates: bool = False,
    ) -> dict:
        inventory = [item for item in request.inventory if item.uid not in excluded_uids]
        self.scoring_engine.roles_db = dict(request.roles_db)
        pools = self.scoring_engine.evaluate_global_inventory(
            inventory,
            top_k_per_shape_per_role=request.drive_screen_limit,
            tape_top_k_per_set_per_role=request.tape_screen_limit,
            tape_main_filters={key: list(value) for key, value in request.core_main_filters.items()},
            crit_priority_modes=dict(request.stat_priority_configs),
        )
        if use_full_drive_candidates:
            # ``all_drives`` 包含同一固定背包中全部已评分驱动；常规
            # ``drives`` 则仍是每角色/形状 Top-K 的并集。
            pools = dict(pools)
            pools["drives"] = list(pools.get("all_drives") or pools.get("drives") or ())
        dispatcher = DispatcherEngine(
            dict(request.roles_db), dict(request.sets_db), dict(request.blueprints_db),
            core_set_targets=dict(request.core_set_targets),
            stat_catalog=self.scoring_engine.stat_catalog,
        )
        result = dispatcher.execute_dispatch(
            request.strategy,
            pools,
            list(request.role_order),
            dict(request.module_set_targets),
            dict(request.stat_priority_configs),
            priority_groups=[list(group) for group in request.priority_groups] or None,
            crit_rate_caps=dict(request.crit_rate_caps or {}),
        )
        self._annotate_missing_core_reasons(request, pools, result)
        return result

    def _annotate_missing_core_reasons(
        self,
        request: AllocationKernelRequest,
        pools: Mapping,
        result: dict,
    ) -> None:
        """Explain why a valid drive plan has no core without changing it."""

        assigned_core_uids = {
            tape.uid
            for plan in result.values()
            if isinstance(plan, Mapping)
            and isinstance((tape := plan.get("assigned_tape")), Tape)
        }
        all_cores = [
            item for item in request.inventory if isinstance(item, Tape)
        ]
        for role in request.role_order:
            plan = result.get(role)
            if (
                not isinstance(plan, dict)
                or not plan.get("valid")
                or isinstance(plan.get("assigned_tape"), Tape)
            ):
                continue
            target_set = (
                request.core_set_targets.get(role)
                if role in request.core_set_targets
                else request.module_set_targets.get(
                    role,
                    str((request.roles_db.get(role) or {}).get("default_set") or ""),
                )
            )
            set_cores = [
                tape
                for tape in all_cores
                if tape_matches_suit_target(tape, target_set, request.sets_db)
            ]
            if not set_cores:
                plan["missing_core_reason"] = (
                    tr("固定快照中没有套装为 {suit} 的卡带", suit=display_term(target_set))
                    if target_set
                    else tr("固定快照中没有任何可用卡带")
                )
                continue
            allowed_mains = tuple(request.core_main_filters.get(role) or ())
            main_cores = [
                tape
                for tape in set_cores
                if not allowed_mains
                or str(tape.main_stats or "").strip() in allowed_mains
                or self.scoring_engine.stat_catalog.normalize_tape_main_stat(
                    str(tape.main_stats or "").strip()
                )
                in allowed_mains
            ]
            if not main_cores:
                plan["missing_core_reason"] = (
                    f"固定快照中没有同时满足 {target_set or '任意套装'}"
                    f"与 {'/'.join(allowed_mains)} 主词条的卡带"
                )
                continue
            screened = [
                tape
                for tape in (pools.get("tapes", {}).get(role, ()) or ())
                if tape_matches_suit_target(tape, target_set, request.sets_db)
            ]
            if not screened:
                plan["missing_core_reason"] = tr(
                    "满足套装和主词条的卡带均被副词条或评分等级硬过滤"
                )
                continue
            available = [
                tape for tape in screened if tape.uid not in assigned_core_uids
            ]
            if not available:
                plan["missing_core_reason"] = (
                    f"满足条件的 {len(screened)} 张唯一卡带已分配给其他角色"
                )
            elif request.crit_rate_caps.get(role) is not None or request.property_limits.get(role):
                plan["missing_core_reason"] = (
                    "候选卡带未通过暴击或属性上下限约束"
                )
            else:
                plan["missing_core_reason"] = (
                    "候选卡带未通过当前角色的硬过滤或唯一分配约束"
                )

    @staticmethod
    def _required_shape_counts(blueprint: Mapping) -> Counter[str]:
        return Counter(
            str(shape)
            for shape in (
                *tuple(blueprint.get("set_pieces") or ()),
                *tuple(blueprint.get("extra_pieces") or ()),
            )
        )

    def _diagnostic_reason(
        self,
        request: AllocationKernelRequest,
        role: str,
        plan: Mapping,
        *,
        full_global_candidates: bool,
    ) -> str:
        """Return an actionable failure reason instead of a legacy catch-all."""

        blueprint = plan.get("blueprint") if isinstance(plan, Mapping) else None
        plan_items = self._plan_items(plan)
        expected_modules = (
            len(tuple((blueprint or {}).get("set_pieces") or ()))
            + len(tuple((blueprint or {}).get("extra_pieces") or ()))
        )
        drives = [item for item in plan_items if isinstance(item, Drive)]
        if blueprint and len(drives) != expected_modules:
            return tr("图纸需要 {expected} 个驱动，当前方案仅分配 {actual} 个",
                      expected=expected_modules, actual=len(drives))
        if len({item.uid for item in plan_items}) != len(plan_items):
            return tr("方案包含重复驱动 UID，无法安全分配")
        if self._violates_limits(
            role, plan_items, request.roles_db.get(role, {}),
            request.property_limits.get(role, ()),
        ):
            return tr("未满足角色属性上下限")

        blueprints = list(request.blueprints_db.get(role) or ())
        if not blueprints:
            return tr("角色没有可用图纸")
        available_shapes = Counter(
            str(item.shape_id)
            for item in request.inventory
            if isinstance(item, Drive)
        )
        shortages: list[tuple[int, Counter[str]]] = []
        for candidate in blueprints:
            required = self._required_shape_counts(candidate)
            missing = Counter({
                shape: count - available_shapes.get(shape, 0)
                for shape, count in required.items()
                if count > available_shapes.get(shape, 0)
            })
            shortages.append((sum(missing.values()), missing))
        _shortage_count, missing = min(shortages, key=lambda value: value[0])
        if missing:
            shape, count = sorted(missing.items(), key=lambda value: (-value[1], value[0]))[0]
            required = min(
                self._required_shape_counts(candidate).get(shape, 0)
                for candidate in blueprints
            )
            return (
                f"缺少 {shape} 驱动：图纸至少需要 {required} 个，"
                f"当前可用 {available_shapes.get(shape, 0)} 个（还缺 {count} 个）"
            )

        for group in request.priority_groups or ():
            if role in group and len(group) > 1:
                return "同级组竞争后无剩余候选：所需形状驱动已被同级角色占用"
        if request.strategy == "global_optimal":
            if full_global_candidates:
                return tr("扩展至完整背包候选后仍无法形成完整全队方案（角色间形状需求冲突）")
            return tr("当前候选范围无法形成完整全队方案")
        return tr("候选驱动未能同时满足图纸形状约束")

    def _apply_invalid_diagnostics(
        self,
        request: AllocationKernelRequest,
        result: dict,
        invalid_roles: Sequence[str],
        *,
        full_global_candidates: bool,
    ) -> None:
        for role in invalid_roles:
            plan = dict(result.get(role) or {})
            plan["valid"] = False
            plan.setdefault(
                "reason",
                self._diagnostic_reason(
                    request, role, plan,
                    full_global_candidates=full_global_candidates,
                ),
            )
            result[role] = plan

    @staticmethod
    def _plan_items(plan: Mapping) -> tuple[Drive | Tape, ...]:
        values: list[Drive | Tape] = []
        tape = plan.get("assigned_tape")
        if isinstance(tape, Tape):
            values.append(tape)
        values.extend(item for item in (plan.get("assigned_set_drives") or ()) if isinstance(item, Drive))
        values.extend(item for item in (plan.get("assigned_extra_drives") or ()) if isinstance(item, Drive))
        return tuple(values)

    def _invalid_roles(self, request: AllocationKernelRequest, result: Mapping[str, Mapping]) -> tuple[str, ...]:
        invalid: list[str] = []
        for role in request.role_order:
            plan = result.get(role) or {}
            if not plan.get("valid"):
                invalid.append(role)
                continue
            expected_modules = len(plan.get("blueprint", {}).get("set_pieces", ())) + len(
                plan.get("blueprint", {}).get("extra_pieces", ())
            )
            items = self._plan_items(plan)
            tape = plan.get("assigned_tape")
            expected_item_count = expected_modules + (1 if isinstance(tape, Tape) else 0)
            if len(items) != expected_item_count or len({item.uid for item in items}) != len(items):
                invalid.append(role)
                continue
            if self._violates_limits(role, items, request.roles_db.get(role, {}), request.property_limits.get(role, ())):
                invalid.append(role)
        return tuple(invalid)

    def _violates_limits(
        self, role: str, items: Sequence[Drive | Tape], role_data: Mapping,
        limits: Sequence[AllocationPropertyLimit],
    ) -> bool:
        if not limits:
            return False
        totals: dict[str, float] = {}
        for item in items:
            for stat_name, value in (getattr(item, "sub_stats", {}) or {}).items():
                totals[stat_name] = totals.get(stat_name, 0.0) + float(value)
            if isinstance(item, Drive):
                for stat_name, value in item.main_stats.items():
                    totals[stat_name] = totals.get(stat_name, 0.0) + float(value)
            elif item.main_stats:
                main_name = str(item.main_stats)
                tape_values = getattr(self.scoring_engine.stat_catalog, "tape_main_values", {}) or {}
                main_value = getattr(item, "main_value", None)
                if main_value is None:
                    main_value = tape_values.get(main_name, 0.0)
                totals[main_name] = totals.get(main_name, 0.0) + float(main_value or 0.0)
        label = str((role_data or {}).get("extra_shape_label", "") or "")
        area_match = re.search(r"(\d+)", label)
        if area_match:
            extra_count = sum(1 for item in items if isinstance(item, Drive) and item.area == int(area_match.group(1)))
            for stat_name, value in ((role_data or {}).get("extra_shape_buffs", {}) or {}).items():
                totals[str(stat_name)] = totals.get(str(stat_name), 0.0) + float(value) * extra_count
        for limit in limits:
            value = totals.get(limit.stat_name, 0.0)
            if limit.minimum is not None and value < float(limit.minimum):
                return True
            if limit.maximum is not None and value > float(limit.maximum):
                return True
        return False
