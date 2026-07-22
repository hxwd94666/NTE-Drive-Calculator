# 为旧 UI 与不可变 Context 提供唯一的内存态配装算法入口。
"""Shared in-memory allocation kernel for legacy and frozen-context callers.

The kernel deliberately owns no scoring formula, puzzle generation or strategy
implementation.  It prepares the established ``ScoringEngine`` and
``DispatcherEngine`` with one explicit request contract, so the old UI and the
v5 Context adapter cannot drift into separate recommendation algorithms.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from src.models.equipment import Drive, Tape
from src.optimizer.dispatcher import DispatcherEngine
from src.optimizer.scoring import ScoringEngine


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
    # The established legacy page requires a core.  The simplified weighted
    # page can still recommend a complete drive layout while clearly marking
    # an unavailable recommended core as empty.
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
    tape_limit = max(6, group_size * 4)
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
        initial = self._execute_once(request, frozenset())
        initial_invalid = self._invalid_roles(request, initial)
        if not has_limits:
            if not initial_invalid:
                return initial
            for role in initial_invalid:
                initial[role] = {"valid": False, "reason": "未满足核心或模块完整性"}
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
            result = self._execute_once(request, excluded)
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

        failed = self._execute_once(request, frozenset())
        for role in self._invalid_roles(request, failed):
            failed[role] = {"valid": False, "reason": "未满足核心、模块或属性限制"}
        return failed

    def _execute_once(self, request: AllocationKernelRequest, excluded_uids: frozenset[str]) -> dict:
        inventory = [item for item in request.inventory if item.uid not in excluded_uids]
        self.scoring_engine.roles_db = dict(request.roles_db)
        pools = self.scoring_engine.evaluate_global_inventory(
            inventory,
            top_k_per_shape_per_role=request.drive_screen_limit,
            tape_top_k_per_set_per_role=request.tape_screen_limit,
            tape_main_filters={key: list(value) for key, value in request.core_main_filters.items()},
            crit_priority_modes=dict(request.stat_priority_configs),
        )
        dispatcher = DispatcherEngine(
            dict(request.roles_db), dict(request.sets_db), dict(request.blueprints_db),
            core_set_targets=dict(request.core_set_targets),
        )
        return dispatcher.execute_dispatch(
            request.strategy,
            pools,
            list(request.role_order),
            dict(request.module_set_targets),
            dict(request.stat_priority_configs),
            priority_groups=[list(group) for group in request.priority_groups] or None,
            crit_rate_caps=dict(request.crit_rate_caps or {}),
        )

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
            if not request.allow_missing_core and not isinstance(tape, Tape):
                invalid.append(role)
                continue
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
                totals[main_name] = totals.get(main_name, 0.0) + float(tape_values.get(main_name, 0.0) or 0.0)
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
