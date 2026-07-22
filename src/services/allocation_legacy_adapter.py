# 将不可变 AllocationContext 适配到既有评分和分配组件。
"""Compatibility adapter for the existing weighted allocation implementation.

This module intentionally contains no scoring or allocation policy.  It builds
the legacy in-memory objects from a frozen context, then invokes the existing
``ScoringEngine``, ``NTEPipelineOrchestrator.solve_blueprints`` and
``DispatcherEngine`` unchanged.  It is the compatibility seam used while the
old and new allocation entries coexist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from src.models.equipment import Drive, DriveShape, Tape
from src.optimizer.allocation_kernel import (
    AllocationKernel, AllocationKernelRequest, AllocationPropertyLimit, estimate_candidate_pool_limits,
)
from src.optimizer.scoring import ScoringEngine
from src.services.allocation_context import AllocationCandidate, AllocationContext, AllocationRolePreference
from src.services.sqlite_allocation_inventory import legacy_stat_name, legacy_stat_value
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


def _legacy_items(context: AllocationContext) -> tuple[list[Drive | Tape], dict[str, AllocationCandidate]]:
    names = _attribute_names(context)
    shapes_by_geometry = {shape.shape_id: shape for shape in context.shapes}
    result: list[Drive | Tape] = []
    by_uid: dict[str, AllocationCandidate] = {}
    for candidate in context.candidates:
        legacy_uid = _uid(candidate)
        by_uid[legacy_uid] = candidate
        if candidate.kind == "module":
            geometry = str(candidate.geometry or "")
            shape = shapes_by_geometry[geometry]
            # model_construct deliberately avoids legacy UI validation: the
            # immutable inventory already underwent official-ID validation.
            result.append(Drive.model_construct(
                uid=legacy_uid, item_type="drive", quality=_quality(candidate.quality),
                area=int(shape.cell_count),
                shape_id=str(shape.legacy_shape_id or shape.shape_id), set_name=str(candidate.suit_id or _NO_SET_PREFIX),
                main_stats=_stats(candidate, names, main=True),
                sub_stats=_stats(candidate, names, main=False),
                discarded=candidate.discarded, is_duplicate_drive=candidate.is_duplicate_drive,
                duplicate_group_id=candidate.duplicate_group_id, duplicate_index=candidate.duplicate_index,
                duplicate_count=candidate.duplicate_count, role_scores={}, max_score=0.0,
                is_mvp=False, pick_order=0,
            ))
        elif candidate.kind == "core":
            main_stats = _stats(candidate, names, main=True)
            result.append(Tape.model_construct(
                uid=legacy_uid, item_type="tape", quality=_quality(candidate.quality), area=15,
                set_name=str(candidate.suit_id or _NO_SET_PREFIX),
                main_stats=next(iter(main_stats), ""), sub_stats=_stats(candidate, names, main=False),
                discarded=candidate.discarded, is_duplicate_drive=candidate.is_duplicate_drive,
                duplicate_group_id=candidate.duplicate_group_id, duplicate_index=candidate.duplicate_index,
                duplicate_count=candidate.duplicate_count, role_scores={}, max_score=0.0,
                is_mvp=False, pick_order=0,
            ))
    return result, by_uid


def _role_key(role: AllocationRolePreference) -> str:
    return f"character:{role.character_id}"


def _weights(role: AllocationRolePreference, names: Mapping[str, str], *, main: bool) -> dict[str, float]:
    source = role.effective_main_property_weights if main else role.effective_property_weights
    return {names.get(property_id, property_id): float(weight) for property_id, weight in source}


def _role_config(
    context: AllocationContext,
    selected_roles: Sequence[AllocationRolePreference],
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
        if priority_names:
            crit_priority_modes[key] = {"stats": priority_names, "ignore_grade_limit": True}
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


def _shapes(context: AllocationContext) -> dict[str, DriveShape]:
    result: dict[str, DriveShape] = {}
    for shape in context.shapes:
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
) -> LegacyAllocationRun:
    """Run the unchanged old scoring, blueprint and strategy pipeline.

    All mutable objects below are short-lived projections of the Context.  The
    old pipeline never sees a DAO, a moving snapshot, or a newer user profile.
    """

    selected_roles = tuple(roles if roles is not None else context.roles)
    excluded = frozenset(excluded_uids)
    inventory, candidate_by_legacy_uid = _legacy_items(context)
    inventory = [item for item in inventory if candidate_by_legacy_uid[item.uid].uid not in excluded]
    (roles_db, suits_db, custom_sets, priority_list, set_modes,
     priority_modes, crit_caps, priority_groups) = _role_config(context, selected_roles)
    if any(not role_data["weights"] for role_data in roles_db.values()):
        missing = [key for key, role_data in roles_db.items() if not role_data["weights"]]
        raise ValueError(f"缺少异环工坊或用户覆盖词条权重：{', '.join(missing)}")
    orchestrator = NTEPipelineOrchestrator.from_frozen_inputs(
        roles_db=roles_db, sets_db=suits_db, shapes_db=_shapes(context),
    )
    blueprints = orchestrator.solve_blueprints(
        priority_list, custom_sets=custom_sets, set_effect_modes=set_modes,
        include_layout_variants=False,
    )
    scoring = ScoringEngine()
    scoring.roles_db = roles_db
    names = _attribute_names(context)
    tape_filters = {
        _role_key(role): (names.get(role.core_main_property_id, role.core_main_property_id),)
        for role in selected_roles if role.core_main_property_id is not None
    }
    core_set_targets = {
        # target_suit_id is also the optional, explicit core preference when
        # module mode is ``none``.  Do not infer it from an official plan.
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
    # v5 core-main preference is a hard selection constraint for every
    # strategy.  Only legacy sub-stat priority and the old critical cap remain
    # role-priority-only behaviour.
    if context.allocation_strategy != "role_priority":
        priority_modes = {}
        crit_caps = {}
    drive_screen_limit, tape_screen_limit = estimate_candidate_pool_limits(
        blueprints, priority_list, priority_groups,
    )
    request = AllocationKernelRequest(
        inventory=tuple(inventory), roles_db=roles_db, sets_db=suits_db, shapes_db=_shapes(context),
        blueprints_db=blueprints, role_order=tuple(priority_list), strategy=context.allocation_strategy,
        module_set_targets=custom_sets, set_effect_modes=set_modes, core_main_filters=tape_filters,
        core_set_targets=core_set_targets, stat_priority_configs=priority_modes,
        property_limits=property_limits, priority_groups=tuple(tuple(group) for group in priority_groups),
        crit_rate_caps=crit_caps, allow_missing_core=allow_missing_core,
        drive_screen_limit=drive_screen_limit, tape_screen_limit=tape_screen_limit,
    )
    plans = AllocationKernel(scoring).execute(request)
    return LegacyAllocationRun(
        role_keys=tuple((role.character_id, _role_key(role)) for role in selected_roles),
        plans=plans, candidates_by_legacy_uid=candidate_by_legacy_uid,
    )
