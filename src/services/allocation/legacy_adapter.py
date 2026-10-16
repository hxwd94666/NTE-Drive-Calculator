# 将唯一冻结输入适配到既有基础评分和分配内核，不新增公式或求解入口。
"""同一ScoringEngine显式消费冻结规则；投影、bulk与取消链保持原实现。"""

from __future__ import annotations

from concurrent.futures import CancelledError
from collections.abc import Callable
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from src.models.equipment import Drive, DriveShape, Tape
from src.optimizer.allocation_kernel import (
    AllocationKernel, AllocationKernelRequest, AllocationPropertyLimit, estimate_candidate_pool_limits,
)
from src.optimizer.scoring import ScoringEngine
from src.domain.allocation.inputs import AllocationCandidate, AllocationContext, AllocationRolePreference
from src.services.allocation.inventory import legacy_stat_name, legacy_stat_value
from src.solver.orchestrator import NTEPipelineOrchestrator


_NO_SET_PREFIX = "__context_no_set__"


@dataclass(frozen=True, slots=True)
class LegacyAllocationRun:
    """The unmodified legacy result plus its frozen-ID lookup tables."""

    role_keys: tuple[tuple[int, str], ...]
    plans: Mapping[str, Mapping]
    candidates_by_legacy_uid: Mapping[str, AllocationCandidate]

    def role_key(self, character_id: int) -> str:
        return dict(self.role_keys)[character_id]


def _quality(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return {"orange": "Gold", "gold": "Gold", "purple": "Purple", "blue": "Blue"}.get(normalized, "Gold")


def _uid(candidate: AllocationCandidate) -> str:
    return f"context:{candidate.uid_slot}:{candidate.uid_serial}"


def _attribute_names(context: AllocationContext) -> dict[str, str]:
    return {
        attribute.property_id: legacy_stat_name(attribute.property_id) or attribute.scoring_name
        for attribute in context.attributes
    }


def _stats(candidate: AllocationCandidate, names: Mapping[str, str], *, main: bool) -> dict[str, float]:
    source = candidate.main_stats if main else candidate.sub_stats
    result: dict[str, float] = {}
    for stat in source:
        result[names.get(stat.property_id, stat.property_id)] = legacy_stat_value(stat.value, stat.percent)
    return result


def _checkpoint(cancel_check):
    if cancel_check is not None and cancel_check():
        raise CancelledError("配装计算已取消")


def _project_candidate(candidate, names, shapes_by_geometry):
    """整次求解与批量替换共用单件无公式投影，保持官方身份边界。"""
    legacy_uid = _uid(candidate)
    if candidate.kind == "module":
        geometry = str(candidate.geometry or "")
        shape = shapes_by_geometry[geometry]
        # 正式ID已在冻结输入边界校验；这里只投影，不重复旧UI模型校验。
        return Drive.model_construct(
            uid=legacy_uid, item_type="drive", quality=_quality(candidate.quality),
            area=int(shape.cell_count),
            shape_id=str(shape.legacy_shape_id or shape.shape_id), set_name=str(candidate.suit_id or _NO_SET_PREFIX),
            main_stats=_stats(candidate, names, main=True),
            sub_stats=_stats(candidate, names, main=False),
            discarded=candidate.discarded, is_duplicate_drive=candidate.is_duplicate_drive,
            duplicate_group_id=candidate.duplicate_group_id, duplicate_index=candidate.duplicate_index,
            duplicate_count=candidate.duplicate_count, role_scores={}, max_score=0.0,
            is_mvp=False, pick_order=0,
        )
    elif candidate.kind == "core":
        main_stats = _stats(candidate, names, main=True)
        main_stat, main_value = next(iter(main_stats.items()), ("", None))
        return Tape.model_construct(
            uid=legacy_uid, item_type="tape", quality=_quality(candidate.quality), area=15,
            suit_id=candidate.suit_id,
            set_name=str(candidate.suit_id or _NO_SET_PREFIX),
            main_stats=main_stat, main_value=main_value, sub_stats=_stats(candidate, names, main=False),
            discarded=candidate.discarded, is_duplicate_drive=candidate.is_duplicate_drive,
            duplicate_group_id=candidate.duplicate_group_id, duplicate_index=candidate.duplicate_index,
            duplicate_count=candidate.duplicate_count, role_scores={}, max_score=0.0,
            is_mvp=False, pick_order=0,
        )
    raise ValueError(f"候选装备{candidate.uid}的类型无效")


def legacy_items(context: AllocationContext, *, cancel_check=None) -> tuple[list[Drive | Tape], dict[str, AllocationCandidate]]:
    names = _attribute_names(context)
    shapes = {shape.shape_id: shape for shape in context.shapes}
    result, by_uid = [], {}
    for candidate in context.candidates:
        _checkpoint(cancel_check)
        by_uid[_uid(candidate)] = candidate
        result.append(_project_candidate(candidate, names, shapes))
    _checkpoint(cancel_check)
    return result, by_uid


def _role_key(role: AllocationRolePreference) -> str:
    return f"character:{role.character_id}"


def _weights(role: AllocationRolePreference, names: Mapping[str, str], *, main: bool) -> dict[str, float]:
    source = role.effective_main_property_weights if main else role.effective_property_weights
    return {names.get(property_id, property_id): float(weight) for property_id, weight in source}


def score_allocation_candidates(
    context: AllocationContext,
    role: AllocationRolePreference,
    candidates: Iterable[AllocationCandidate],
) -> dict[tuple[int, int], float]:
    """单次请求建立O(N)身份索引，仅投影并评分所请求的O(M)候选。"""
    canonical_role = next((value for value in context.roles if value.character_id == role.character_id), None)
    if canonical_role is None or canonical_role != role:
        raise ValueError("候选评分角色不属于固定上下文")
    by_uid = {candidate.uid: candidate for candidate in context.candidates}
    names = _attribute_names(context)
    shapes = {shape.shape_id: shape for shape in context.shapes}
    weights = _weights(canonical_role, names, main=False)
    mains = _weights(canonical_role, names, main=True)
    scoring = ScoringEngine(stat_catalog=context.scoring_rules.to_stat_catalog(), roles_db={})
    zeros = ([names.get(pid, pid) for pid in canonical_role.substat_blacklist]
             if canonical_role.blacklist_zero_weight else ())
    items = {}
    for requested in candidates:
        candidate = by_uid.get(requested.uid)
        if candidate is None:
            raise ValueError(f"候选装备{requested.uid}不在固定背包上下文中")
        if candidate.uid not in items:
            items[candidate.uid] = _project_candidate(candidate, names, shapes)
    scoring.prepare_scores(list(items.values()), profiles=[{"key": "profile", "weights": weights,
        "main_weights": mains, "zero_weight_stats": list(zeros)}])
    maximum = scoring.max_theoretical_weight(weights, zero_weight_stats=zeros)
    scores = {uid: float(scoring.calculate_drive_score(item, weights, maximum, zero_weight_stats=zeros)
                        if isinstance(item, Drive) else scoring.calculate_cartridge_score(item, weights, maximum, mains))
              for uid, item in items.items()}

    return scores


def score_allocation_candidate(context, role, candidate) -> float:
    """确认替换时独立重算一次，不信任UI分数也不扫描投影整背包。"""
    return score_allocation_candidates(context, role, (candidate,))[candidate.uid]


def _role_config(
    context: AllocationContext,
    selected_roles: Sequence[AllocationRolePreference],
    *, cancel_check=None,
) -> tuple[dict, dict, dict, list[str], dict, dict, dict, list[list[str]]]:
    names = _attribute_names(context)
    legacy_shape_ids = {
        shape.shape_id: str(shape.legacy_shape_id or shape.shape_id)
        for shape in context.shapes
    }
    suits = {
        suit.suit_id: {
            "shapes": [legacy_shape_ids.get(shape_id, shape_id) for shape_id in suit.required_shape_ids]
        }
        for suit in context.suits
    }
    roles_db: dict = {}
    custom_sets: dict = {}
    set_modes: dict = {}
    priority_groups: dict[int, list[str]] = {}
    crit_priority_modes: dict = {}
    crit_rate_caps: dict = {}
    for role in selected_roles:
        _checkpoint(cancel_check)
        key = _role_key(role)
        target_set = role.target_suit_id if role.suit_requirement_mode != "none" else f"{_NO_SET_PREFIX}:{role.character_id}"
        suits.setdefault(target_set, {"shapes": []})
        roles_db[key] = {
            "weights": _weights(role, names, main=False),
            "main_weights": _weights(role, names, main=True),
            "default_set": target_set,
            "extra_shape_label": role.extra_shape_label,
            "extra_shape_buffs": {names.get(property_id, property_id): value for property_id, value in role.extra_shape_buffs},
            "board_matrix": _board(role),
        }
        custom_sets[key] = target_set
        set_modes[key] = role.suit_requirement_mode
        priority_groups.setdefault(role.priority_group, []).append(key)
        priority_names = [names.get(property_id, property_id) for property_id in role.substat_priorities]
        blacklist_names = [names.get(property_id, property_id) for property_id in role.substat_blacklist]
        if (
            priority_names
            or blacklist_names
            or role.equal_substat_priority
            or role.blacklist_zero_weight
            or role.ignore_grade_limit
            or role.min_grade_limit != "A"
            or role.crit_threshold is not None
        ):
            crit_priority_modes[key] = {
                "stats": priority_names,
                "blacklist": blacklist_names,
                "blacklist_zero_weight": role.blacklist_zero_weight,
                "equal_priority": role.equal_substat_priority,
                "ignore_grade_limit": role.ignore_grade_limit,
                "min_grade_limit": role.min_grade_limit,
                "crit_threshold": role.crit_threshold,
            }
        for limit in role.property_limits:
            label = names.get(limit.property_id, limit.property_id)
            if limit.maximum is not None and "暴击率" in label:
                crit_rate_caps[key] = float(limit.maximum)
    ordered = sorted(selected_roles, key=lambda role: (role.priority_group, role.ordinal))
    return roles_db, suits, custom_sets, [_role_key(role) for role in ordered], set_modes, crit_priority_modes, crit_rate_caps, [priority_groups[index] for index in sorted(priority_groups)]


def _board(role: AllocationRolePreference) -> list[list[int]]:
    board = [[-1] * 5 for _ in range(5)]
    for cell in role.equipment.cells:
        board[cell.row - 1][cell.column - 1] = 0
    return board


def legacy_shapes(context: AllocationContext, *, cancel_check=None) -> dict[str, DriveShape]:
    result: dict[str, DriveShape] = {}
    for shape in context.shapes:
        _checkpoint(cancel_check)
        xs = [cell.x for cell in shape.cells]
        ys = [cell.y for cell in shape.cells]
        matrix = [[0] * (max(ys) - min(ys) + 1) for _ in range(max(xs) - min(xs) + 1)]
        for cell in shape.cells:
            matrix[cell.x - min(xs)][cell.y - min(ys)] = 1
        legacy_shape_id = str(shape.legacy_shape_id or shape.shape_id)
        legacy_label = str(shape.legacy_label or legacy_shape_id)
        result[legacy_shape_id] = DriveShape(
            shape_id=legacy_shape_id, label=legacy_label, matrix=matrix,
            area=shape.cell_count, description=shape.shape_id,
        )
    return result


def run_legacy_allocation(
    context: AllocationContext,
    *,
    roles: Sequence[AllocationRolePreference] | None = None,
    excluded_uids: Iterable[tuple[int, int]] = (),
    allow_missing_core: bool = False,
    cancel_check: Callable[[], bool] | None = None,
) -> LegacyAllocationRun:
    """Run the unchanged old scoring, blueprint and strategy pipeline.

    All mutable objects below are short-lived projections of the Context.  The
    old pipeline never sees a DAO, a moving snapshot, or a newer user profile.
    """

    _checkpoint(cancel_check)
    selected_roles = tuple(roles if roles is not None else context.roles)
    excluded = frozenset(excluded_uids)
    inventory, candidate_by_legacy_uid = legacy_items(context, cancel_check=cancel_check)
    inventory = [item for item in inventory if candidate_by_legacy_uid[item.uid].uid not in excluded]
    (roles_db, suits_db, custom_sets, priority_list, set_modes,
     priority_modes, crit_caps, priority_groups) = _role_config(context, selected_roles, cancel_check=cancel_check)
    if any(not role_data["weights"] for role_data in roles_db.values()):
        missing = [key for key, role_data in roles_db.items() if not role_data["weights"]]
        raise ValueError(f"缺少异环工坊或用户覆盖词条权重：{', '.join(missing)}")
    _checkpoint(cancel_check)
    shapes_db = legacy_shapes(context, cancel_check=cancel_check)
    orchestrator = NTEPipelineOrchestrator.from_frozen_inputs(
        roles_db=roles_db, sets_db=suits_db, shapes_db=shapes_db,
    )
    blueprints = orchestrator.solve_blueprints(
        priority_list, custom_sets=custom_sets, set_effect_modes=set_modes,
        include_layout_variants=False, cancel_check=cancel_check,
    )
    _checkpoint(cancel_check)
    scoring = ScoringEngine(stat_catalog=context.scoring_rules.to_stat_catalog(), roles_db=roles_db)
    names = _attribute_names(context)
    tape_filters = {
        _role_key(role): (names.get(role.core_main_property_id, role.core_main_property_id),)
        for role in selected_roles if role.core_main_property_id is not None
    }
    core_set_targets = {
        # 模组模式none时target_suit_id仍是显式可选卡带偏好，不从官方方案推断。
        _role_key(role): role.target_suit_id
        for role in selected_roles
    }
    property_limits = {
        _role_key(role): tuple(
            AllocationPropertyLimit(names.get(limit.property_id, limit.property_id), limit.minimum, limit.maximum)
            for limit in role.property_limits
        )
        for role in selected_roles
    }
    # 冻结核心主词条偏好保持硬约束；旧优先模式和暴击上限投影只用于role_priority。
    if context.allocation_strategy != "role_priority":
        priority_modes = {}
        crit_caps = {}
    drive_screen_limit, tape_screen_limit = estimate_candidate_pool_limits(
        blueprints, priority_list, priority_groups,
    )
    request = AllocationKernelRequest(
        inventory=tuple(inventory), roles_db=roles_db, sets_db=suits_db, shapes_db=shapes_db,
        blueprints_db=blueprints, role_order=tuple(priority_list), strategy=context.allocation_strategy,
        module_set_targets=custom_sets, set_effect_modes=set_modes, core_main_filters=tape_filters,
        core_set_targets=core_set_targets, stat_priority_configs=priority_modes,
        property_limits=property_limits, priority_groups=tuple(tuple(group) for group in priority_groups),
        crit_rate_caps=crit_caps, allow_missing_core=allow_missing_core,
        drive_screen_limit=drive_screen_limit, tape_screen_limit=tape_screen_limit,
        cancel_check=cancel_check,
    )
    plans = AllocationKernel(scoring).execute(request)
    _checkpoint(cancel_check)
    return LegacyAllocationRun(
        role_keys=tuple((role.character_id, _role_key(role)) for role in selected_roles),
        plans=plans, candidates_by_legacy_uid=candidate_by_legacy_uid,
    )
