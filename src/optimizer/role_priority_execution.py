# 编排角色优先分配、同级组结算及同分驱动的延迟归属。
"""Execution shell for the role-priority optimizer.

Keeping this orchestration outside the group-matching mixin keeps each source
file below the repository size limit while making reservation commits explicit.
"""

from __future__ import annotations

from typing import Any

from src.models.equipment import Drive, Tape
from src.optimizer.deferred_drive_reservations import DeferredDriveReservationState
from src.optimizer.reservation_recovery_support import ProtectedDriveScreenCache
from src.utils.logger import logger


def execute_role_priority(
    strategy: Any,
    candidate_pool: dict,
    priority_list: list[str],
    custom_sets: dict[str, str],
    crit_priority_modes: dict[str, dict] | None = None,
    priority_groups: list[list[str]] | None = None,
    crit_rate_caps: dict[str, float] | None = None,
) -> dict:
    """Allocate priority groups while retaining only exact interchangeable UIDs."""

    logger.info("启动分配模式: 角色优先")
    drives_pool = sorted(list(candidate_pool.get("drives", [])), key=lambda drive: drive.uid)
    full_drives = list(candidate_pool.get("all_drives") or drives_pool)
    tapes_pool = candidate_pool.get("tapes", {})
    crit_priority_modes = crit_priority_modes or {}
    crit_rate_caps = crit_rate_caps or {}
    priority_groups = strategy._normalize_priority_groups(priority_list, priority_groups)
    assigned_tapes: dict[str, Tape | None] = {}
    final_allocation: dict = {}
    used_tape_uids: set[str] = set()
    occupied_drive_uids: set[str] = set()
    reservations = DeferredDriveReservationState()
    recovery_cache = ProtectedDriveScreenCache(strategy, full_drives, crit_priority_modes)

    for group_index, group in enumerate(priority_groups):
        tape_eligible_group = [role for role in group if strategy.blueprints_db.get(role)]
        available_tapes = {
            role: [
                tape for tape in tapes_pool.get(role, [])
                if tape.uid not in used_tape_uids
            ]
            for role in tape_eligible_group
        }
        current_tapes = strategy._pre_allocate_tapes_for_groups(
            [tape_eligible_group] if tape_eligible_group else [],
            custom_sets,
            available_tapes,
            crit_priority_modes,
        )
        for role in group:
            assigned_tapes[role] = current_tapes.get(role)

        if len(group) > 1:
            group_allocation = _choose_group_allocation(
                strategy,
                group,
                drives_pool,
                full_drives,
                custom_sets,
                assigned_tapes,
                crit_priority_modes,
                crit_rate_caps,
                occupied_drive_uids,
                tapes_pool,
                used_tape_uids,
                reservations,
                int(candidate_pool.get("drive_screen_limit") or 15),
                recovery_cache,
            )
        else:
            role_name = group[0]
            group_allocation = {
                role_name: _choose_single_role_plan(
                    strategy,
                    role_name,
                    drives_pool,
                    assigned_tapes,
                    tapes_pool,
                    used_tape_uids,
                    custom_sets,
                    crit_priority_modes,
                    crit_rate_caps,
                    reservations,
                    full_drives,
                    occupied_drive_uids,
                    int(candidate_pool.get("drive_screen_limit") or 15),
                    recovery_cache,
                )
            }

        used_uids = strategy._allocated_drive_uids(group_allocation)
        if not reservations.can_consume(used_uids):
            # A recovery routine must never hand a conflicting fallback to the
            # commit path.  Keep this boundary guard so one failed role cannot
            # abort the complete role-priority calculation.
            logger.error("预留恢复未返回可回填图纸: 角色组={}", ",".join(group))
            group_allocation = {
                role_name: {"valid": False, "reason": "前序同分驱动无法一对一回填"}
                for role_name in group
            }
            used_uids = set()
        final_allocation.update(group_allocation)
        used_tape_uids.update(
            tape.uid
            for plan in group_allocation.values()
            for tape in [plan.get("assigned_tape")]
            if plan.get("valid") and isinstance(tape, Tape)
        )
        for role in group:
            if not group_allocation.get(role, {}).get("valid"):
                assigned_tapes[role] = None

        prior_reservation_uids = reservations.reservation_uids
        reservations.commit(used_uids & prior_reservation_uids)
        registration = strategy._register_deferred_group_slots(
            reservations,
            group_index,
            group_allocation,
            full_drives,
            crit_priority_modes,
            crit_rate_caps,
            occupied_drive_uids,
        )
        occupied_drive_uids.update(registration.fixed_uids)
        drives_pool = _updated_drives_pool(
            drives_pool,
            registration.fixed_uids | reservations.consumed_uids,
            registration.exposed_drives,
        )

    _finalize_deferred_slots(final_allocation, reservations)
    return final_allocation


def _updated_drives_pool(
    current: list[Drive],
    removed_uids: frozenset[str],
    exposed: tuple[Drive, ...],
) -> list[Drive]:
    by_uid = {
        drive.uid: drive for drive in current if drive.uid not in removed_uids
    }
    for drive in exposed:
        if drive.uid not in removed_uids:
            by_uid.setdefault(drive.uid, drive)
    return [by_uid[uid] for uid in sorted(by_uid)]


def _choose_group_allocation(
    strategy: Any,
    group: list[str],
    drives_pool: list[Drive],
    full_drives: list[Drive],
    custom_sets: dict[str, str],
    assigned_tapes: dict[str, Tape | None],
    crit_priority_modes: dict[str, dict],
    crit_rate_caps: dict[str, float],
    occupied_drive_uids: set[str],
    tapes_pool: dict,
    used_tape_uids: set[str],
    reservations: DeferredDriveReservationState,
    candidate_limit: int,
    recovery_cache: ProtectedDriveScreenCache | None = None,
) -> dict:
    """Run ordinary Top-K detection, then progressive and exact recovery."""

    allocation = _normal_group_allocation(
        strategy, group, drives_pool, full_drives, custom_sets, assigned_tapes,
        crit_priority_modes, crit_rate_caps, occupied_drive_uids, tapes_pool,
        used_tape_uids, candidate_limit,
    )
    ordinary_allocation = allocation
    if _allocation_is_reservation_feasible(strategy, allocation, group, reservations):
        return allocation
    if not _allocation_has_full_plan(allocation, group):
        # No deferred candidate was consumed.  Preserve the ordinary solver's
        # constraint-specific failure reason instead of relabelling it as a
        # reservation failure.
        return allocation

    protected_uids: set[str] = set()
    while _allocation_has_full_plan(allocation, group):
        _check_cancelled(strategy)
        used_uids = strategy._allocated_drive_uids(allocation)
        blocker = _next_progressive_protection(reservations, used_uids, protected_uids)
        if blocker is None:
            break
        protected_uids.add(blocker)
        available = _rescreen_protected_drive_types(
            strategy, group, drives_pool, full_drives, protected_uids,
            reservations, candidate_limit, crit_priority_modes, occupied_drive_uids,
            recovery_cache,
        )
        allocation = _normal_group_allocation(
            strategy, group, available, full_drives, custom_sets, assigned_tapes,
            crit_priority_modes, crit_rate_caps, occupied_drive_uids, tapes_pool,
            used_tape_uids, candidate_limit,
        )
        if _allocation_is_reservation_feasible(strategy, allocation, group, reservations):
            _log_recovery(
                group, "递进保护", ordinary_allocation, allocation,
                protected_uids, reservations, strategy, crit_priority_modes, crit_rate_caps,
            )
            return allocation
    return _final_group_rescue(
        strategy, group, full_drives, custom_sets, assigned_tapes,
        crit_priority_modes, crit_rate_caps, occupied_drive_uids,
        reservations, tapes_pool, used_tape_uids,
    )


def _choose_single_role_plan(
    strategy: Any,
    role_name: str,
    drives_pool: list[Drive],
    assigned_tapes: dict[str, Tape | None],
    tapes_pool: dict,
    used_tape_uids: set[str],
    custom_sets: dict[str, str],
    crit_priority_modes: dict[str, dict],
    crit_rate_caps: dict[str, float],
    reservations: DeferredDriveReservationState,
    full_drives: list[Drive],
    occupied_drive_uids: set[str],
    candidate_limit: int,
    recovery_cache: ProtectedDriveScreenCache | None = None,
) -> dict:
    plan = _best_single_role_plan(
        strategy, role_name, drives_pool, assigned_tapes, tapes_pool,
        used_tape_uids, custom_sets, crit_priority_modes, crit_rate_caps,
    )
    if plan.get("valid") and reservations.can_consume(
        strategy._allocated_drive_uids({role_name: plan})
    ):
        plan.pop("rank_score", None)
        return plan
    if not plan.get("valid"):
        plan.pop("rank_score", None)
        return plan

    protected_uids: set[str] = set()
    ordinary_plan = plan
    while plan.get("valid"):
        _check_cancelled(strategy)
        blocker = _next_progressive_protection(
            reservations, strategy._allocated_drive_uids({role_name: plan}), protected_uids,
        )
        if blocker is None:
            break
        protected_uids.add(blocker)
        available = _rescreen_protected_drive_types(
            strategy, [role_name], drives_pool, full_drives, protected_uids,
            reservations, candidate_limit, crit_priority_modes, occupied_drive_uids,
            recovery_cache,
        )
        plan = _best_single_role_plan(
            strategy, role_name, available, assigned_tapes, tapes_pool,
            used_tape_uids, custom_sets, crit_priority_modes, crit_rate_caps,
        )
        if plan.get("valid") and reservations.can_consume(
            strategy._allocated_drive_uids({role_name: plan})
        ):
            _log_recovery(
                [role_name], "递进保护", {role_name: ordinary_plan},
                {role_name: plan}, protected_uids, reservations,
                strategy, crit_priority_modes, crit_rate_caps,
            )
            plan.pop("rank_score", None)
            return plan
    result = _final_single_rescue(
        strategy, role_name, full_drives, assigned_tapes, tapes_pool,
        used_tape_uids, custom_sets, crit_priority_modes, crit_rate_caps,
        occupied_drive_uids, reservations,
    )
    result.pop("rank_score", None)
    return result


def _normal_group_allocation(
    strategy: Any,
    group: list[str],
    drives_pool: list[Drive],
    full_drives: list[Drive],
    custom_sets: dict[str, str],
    assigned_tapes: dict[str, Tape | None],
    crit_priority_modes: dict[str, dict],
    crit_rate_caps: dict[str, float],
    occupied_drive_uids: set[str],
    tapes_pool: dict,
    used_tape_uids: set[str],
    candidate_limit: int,
) -> dict:
    """Keep the ordinary group path untouched; reservation is detection only."""

    allocation = strategy._find_best_group_fit(
        group, drives_pool, custom_sets, assigned_tapes,
        crit_priority_modes, crit_rate_caps,
    )
    failed_roles = [role for role in group if not allocation.get(role, {}).get("valid")]
    if not failed_roles:
        return allocation
    return strategy._recover_equal_priority_group(
        group, drives_pool, custom_sets, assigned_tapes,
        crit_priority_modes, crit_rate_caps,
        full_drives=full_drives,
        occupied_uids=occupied_drive_uids,
        tapes_pool=tapes_pool,
        used_tape_uids=used_tape_uids,
        candidate_limit=candidate_limit,
    )


def _allocation_has_full_plan(allocation: dict, group: list[str]) -> bool:
    return all(allocation.get(role, {}).get("valid") for role in group)


def _allocation_is_reservation_feasible(
    strategy: Any,
    allocation: dict,
    group: list[str],
    reservations: DeferredDriveReservationState,
) -> bool:
    return _allocation_has_full_plan(allocation, group) and reservations.can_consume(
        strategy._allocated_drive_uids(allocation)
    )


def _next_progressive_protection(
    reservations: DeferredDriveReservationState,
    used_uids: set[str],
    protected_uids: set[str],
) -> str | None:
    """Protect one UID from the failing plan, ordered by frozen owner score."""

    candidates = set(reservations.effective_blocker_uids(used_uids)) - protected_uids
    if not candidates:
        return None
    return min(candidates, key=reservations.protection_key)


def _log_recovery(
    group: list[str],
    path: str,
    ordinary: dict,
    committed: dict,
    protected_uids: set[str],
    reservations: DeferredDriveReservationState,
    strategy: Any,
    crit_priority_modes: dict[str, dict],
    crit_rate_caps: dict[str, float],
) -> None:
    """Write one compact, UID-free result record for a recovered group."""

    before_key = _allocation_quality_key(ordinary)
    after_key = _allocation_quality_key(committed)
    before_score = _allocation_score(ordinary)
    after_score = _allocation_score(committed)
    delta = f"{before_score - after_score:.2f}" if before_key[0] == after_key[0] else "词条优先级变化"
    shape_counts: dict[str, int] = {}
    for uid in protected_uids:
        shape = reservations.shape_for_uid(uid)
        if shape is not None:
            shape_counts[shape] = shape_counts.get(shape, 0) + 1
    affected = "/".join(f"{shape}×{count}" for shape, count in sorted(shape_counts.items())) or "无"
    constraints = _recovery_constraint_status(
        strategy, group, crit_priority_modes, crit_rate_caps,
    )
    logger.info(
        "同分恢复：角色/组={}，路径={}，常规排序分={:.2f}，提交排序分={:.2f}，"
        "排序差={}，有效阻塞数={}，保护轮数={}，受影响类型={}，前序回填=成功，暴击/词条约束={}",
        ",".join(group), path, before_score, after_score, delta,
        len(protected_uids), len(protected_uids), affected, constraints,
    )


def _recovery_constraint_status(
    strategy: Any,
    group: list[str],
    crit_priority_modes: dict[str, dict],
    crit_rate_caps: dict[str, float],
) -> str:
    """Describe enabled constraint families without exposing their payloads."""

    normalize = getattr(strategy, "_stat_priority_config", None)
    crit_floor = getattr(strategy, "_crit_floor_threshold", None)
    crit_cap = getattr(strategy, "_crit_rate_cap", None)
    if not all(callable(callback) for callback in (normalize, crit_floor, crit_cap)):
        return "未启用"
    crit_enabled = False
    stat_enabled = False
    blacklist_enabled = False
    for role in group:
        config = crit_priority_modes.get(role)
        normalized = normalize(config)
        stat_enabled = stat_enabled or bool(normalized.get("stats"))
        blacklist_enabled = blacklist_enabled or bool(normalized.get("blacklist"))
        crit_enabled = crit_enabled or (
            crit_floor(config) is not None
            or crit_cap(role, crit_rate_caps) is not None
        )
    values = []
    if crit_enabled:
        values.append("暴击已启用且通过")
    if stat_enabled:
        values.append("词条优先已启用且通过")
    if blacklist_enabled:
        values.append("词条黑名单已启用且通过")
    return "；".join(values) if values else "未启用"


def _allocation_score(allocation: dict) -> float:
    return sum(float(plan.get("score", 0.0)) for plan in allocation.values())


def _allocation_quality_key(allocation: dict) -> tuple:
    return (
        tuple(tuple(plan.get("stat_priority_key", ()) or ()) for _, plan in sorted(allocation.items())),
        sum(float(plan.get("rank_score", plan.get("score", 0.0))) for plan in allocation.values()),
        _allocation_score(allocation),
    )


def _rescreen_protected_drive_types(
    strategy: Any,
    group: list[str],
    current_pool: list[Drive],
    full_drives: list[Drive],
    protected_uids: set[str],
    reservations: DeferredDriveReservationState,
    candidate_limit: int,
    crit_priority_modes: dict[str, dict],
    occupied_drive_uids: set[str],
    recovery_cache: ProtectedDriveScreenCache,
) -> list[Drive]:
    """Rebuild Top-K only for types touched by progressive protection."""

    affected_shapes = {
        shape for uid in protected_uids
        if (shape := reservations.shape_for_uid(uid)) is not None
    }
    if not affected_shapes:
        return [drive for drive in current_pool if drive.uid not in protected_uids]
    retained = {
        drive.uid: drive
        for drive in current_pool
        if drive.shape_id not in affected_shapes and drive.uid not in protected_uids
    }
    unavailable = occupied_drive_uids | reservations.consumed_uids | protected_uids
    if not hasattr(strategy, "_item_allowed_for_role"):
        source = [
            drive for drive in full_drives
            if drive.shape_id in affected_shapes and drive.uid not in unavailable
        ]
        retained.update({drive.uid: drive for drive in source})
        return [retained[uid] for uid in sorted(retained)]
    cache = recovery_cache or ProtectedDriveScreenCache(strategy, full_drives, crit_priority_modes)
    retained.update({drive.uid: drive for drive in cache.select(
        group, affected_shapes, candidate_limit, unavailable,
    )})
    return [retained[uid] for uid in sorted(retained)]


def _check_cancelled(strategy: Any) -> None:
    callback = getattr(strategy, "_check_cancelled", None)
    if callback is not None:
        callback()


def _final_rescue_pool(
    full_drives: list[Drive],
    occupied_drive_uids: set[str],
    reservations: DeferredDriveReservationState,
) -> list[Drive]:
    unavailable = occupied_drive_uids | reservations.consumed_uids
    return [drive for drive in full_drives if drive.uid not in unavailable]


def _final_group_rescue(
    strategy: Any,
    group: list[str],
    full_drives: list[Drive],
    custom_sets: dict[str, str],
    assigned_tapes: dict[str, Tape | None],
    crit_priority_modes: dict[str, dict],
    crit_rate_caps: dict[str, float],
    occupied_drive_uids: set[str],
    reservations: DeferredDriveReservationState,
    tapes_pool: dict,
    used_tape_uids: set[str],
) -> dict:
    """Use a full-inventory, per-type one-to-one recovery as the last path."""

    full_pool = _final_rescue_pool(full_drives, occupied_drive_uids, reservations)
    slots = reservations.remaining_slots
    recovered = strategy._find_best_group_fit(
        group, full_pool, custom_sets, assigned_tapes,
        crit_priority_modes, crit_rate_caps,
        reservation_candidates=tuple(slot.candidate_uids for slot in slots),
        reservation_shapes=tuple(slot.shape_id for slot in slots),
    )
    if _allocation_is_reservation_feasible(strategy, recovered, group, reservations):
        logger.info("同级组最终一对一分配恢复完成: 角色数={}", len(group))
        return recovered
    constrained = any(
        strategy._crit_floor_threshold(crit_priority_modes.get(role)) is not None
        or strategy._crit_rate_cap(role, crit_rate_caps) is not None
        for role in group
    )
    if constrained:
        repaired = _final_constrained_group_rescue(
            strategy, group, full_pool, assigned_tapes, tapes_pool, used_tape_uids,
            custom_sets, crit_priority_modes, crit_rate_caps, reservations,
        )
        if repaired is not None:
            logger.info("同级组最终约束恢复完成: 角色数={}", len(group))
            return repaired
    logger.info("同级组最终一对一分配无完整图纸: 角色数={}", len(group))
    return {
        role_name: {"valid": False, "reason": "完整背包无法满足图纸与前序回填"}
        for role_name in group
    }


def _final_single_rescue(
    strategy: Any,
    role_name: str,
    full_drives: list[Drive],
    assigned_tapes: dict[str, Tape | None],
    tapes_pool: dict,
    used_tape_uids: set[str],
    custom_sets: dict[str, str],
    crit_priority_modes: dict[str, dict],
    crit_rate_caps: dict[str, float],
    occupied_drive_uids: set[str],
    reservations: DeferredDriveReservationState,
) -> dict:
    """Try every legal tape with the complete per-type matching recovery."""

    full_pool = _final_rescue_pool(full_drives, occupied_drive_uids, reservations)
    role_config = crit_priority_modes.get(role_name)
    tape_candidates = strategy._tape_candidates_for_capped_role(
        role_name, assigned_tapes, tapes_pool, used_tape_uids,
        custom_sets, role_config,
    ) if (
        strategy._crit_rate_cap(role_name, crit_rate_caps) is not None
        or strategy._crit_floor_threshold(role_config) is not None
    ) else [assigned_tapes.get(role_name)]
    slots = reservations.remaining_slots
    best_plan: dict | None = None
    for tape in tape_candidates:
        _check_cancelled(strategy)
        temporary_tapes = dict(assigned_tapes)
        temporary_tapes[role_name] = tape
        recovered = strategy._find_best_group_fit(
            [role_name], full_pool, custom_sets, temporary_tapes,
            crit_priority_modes, crit_rate_caps,
            reservation_candidates=tuple(slot.candidate_uids for slot in slots),
            reservation_shapes=tuple(slot.shape_id for slot in slots),
        )
        plan = recovered.get(role_name, {"valid": False})
        if not plan.get("valid") or not reservations.can_consume(
            strategy._allocated_drive_uids({role_name: plan})
        ):
            continue
        if best_plan is None or _plan_quality_key(plan) > _plan_quality_key(best_plan):
            best_plan = plan
    if best_plan is not None:
        logger.info("单角色最终一对一分配恢复完成: 角色={}", role_name)
        return best_plan
    if (
        strategy._crit_rate_cap(role_name, crit_rate_caps) is not None
        or strategy._crit_floor_threshold(role_config) is not None
    ):
        repaired, _reason = strategy._repair_role_plans(
            role_name,
            full_pool,
            {drive.uid for drive in full_pool},
            tapes_pool,
            assigned_tapes,
            used_tape_uids,
            custom_sets,
            role_config,
            crit_rate_caps,
            max(len(full_pool), 15),
        )
        feasible = [
            plan for plan in repaired
            if reservations.can_consume(strategy._allocated_drive_uids({role_name: plan}))
        ]
        if feasible:
            return max(feasible, key=_plan_quality_key)
    return {"valid": False, "reason": "完整背包无法满足图纸与前序回填"}


def _final_constrained_group_rescue(
    strategy: Any,
    group: list[str],
    full_pool: list[Drive],
    assigned_tapes: dict[str, Tape | None],
    tapes_pool: dict,
    used_tape_uids: set[str],
    custom_sets: dict[str, str],
    crit_priority_modes: dict[str, dict],
    crit_rate_caps: dict[str, float],
    reservations: DeferredDriveReservationState,
) -> dict | None:
    """Combine bounded constraint-valid role plans while retaining reservations."""

    states: list[tuple[dict, set[str], set[str]]] = [({}, set(), set())]
    available_uids = {drive.uid for drive in full_pool}
    for role in group:
        _check_cancelled(strategy)
        plans, _reason = strategy._repair_role_plans(
            role, full_pool, available_uids, tapes_pool, assigned_tapes,
            used_tape_uids, custom_sets, crit_priority_modes.get(role),
            crit_rate_caps, max(len(full_pool), 15),
        )
        next_states: list[tuple[dict, set[str], set[str]]] = []
        for allocation, drive_uids, tape_uids in states:
            for plan in plans:
                plan_uids = strategy._allocated_drive_uids({role: plan})
                tape = plan.get("assigned_tape")
                next_tape_uids = tape_uids | ({tape.uid} if isinstance(tape, Tape) else set())
                next_drive_uids = drive_uids | plan_uids
                if (
                    drive_uids & plan_uids
                    or tape_uids & ({tape.uid} if isinstance(tape, Tape) else set())
                    or not reservations.can_consume(next_drive_uids)
                ):
                    continue
                next_states.append((
                    {**allocation, role: plan}, next_drive_uids, next_tape_uids,
                ))
        if not next_states:
            return None
        next_states.sort(
            key=lambda state: (
                sum(float(plan.get("rank_score", plan.get("score", 0.0))) for plan in state[0].values()),
                sum(float(plan.get("score", 0.0)) for plan in state[0].values()),
            ),
            reverse=True,
        )
        states = next_states[:64]
    return states[0][0] if states else None


def _plan_quality_key(plan: dict) -> tuple:
    """Rank feasible single-role branches with the optimizer's own order."""

    return (
        tuple(plan.get("stat_priority_key", ()) or ()),
        float(plan.get("rank_score", plan.get("score", -1.0))),
        float(plan.get("score", -1.0)),
    )


def _best_single_role_plan(
    strategy: Any,
    role_name: str,
    drives_pool: list[Drive],
    assigned_tapes: dict[str, Tape | None],
    tapes_pool: dict,
    used_tape_uids: set[str],
    custom_sets: dict[str, str],
    crit_priority_modes: dict[str, dict],
    crit_rate_caps: dict[str, float],
) -> dict:
    blueprints = strategy._dedupe_blueprints_for_role_priority(
        strategy.blueprints_db.get(role_name, []),
    )
    target_set = strategy._target_set(role_name, custom_sets)
    required_shapes = strategy._required_shapes_for_role_blueprints(
        role_name, blueprints, custom_sets,
    )
    role_drives_pool = strategy._filter_drives_by_shapes(drives_pool, required_shapes)
    logger.info(
        "  [{}] 匹配中... (图纸数: {}, 候选池: {})",
        role_name, len(blueprints), len(role_drives_pool),
    )
    best_plan: dict = {
        "valid": False, "score": -1.0, "rank_score": -1.0,
        "stat_priority_key": (),
    }
    failure_reasons: list[str] = []
    role_crit_config = crit_priority_modes.get(role_name)
    retry_tape_candidates = (
        strategy._crit_rate_cap(role_name, crit_rate_caps) is not None
        or strategy._crit_floor_threshold(role_crit_config) is not None
    )
    tape_candidates = (
        strategy._tape_candidates_for_capped_role(
            role_name, assigned_tapes, tapes_pool, used_tape_uids,
            custom_sets, role_crit_config,
        )
        if retry_tape_candidates else [assigned_tapes.get(role_name)]
    )
    for blueprint in blueprints:
        for role_tape in tape_candidates:
            tape_score = role_tape.role_scores.get(role_name, 0.0) if role_tape else 0.0
            if retry_tape_candidates:
                plan = strategy._find_best_fit(
                    role_name, blueprint, role_drives_pool, target_set,
                    role_crit_config, role_tape, crit_rate_caps,
                )
            else:
                plan = strategy._find_best_fit(
                    role_name, blueprint, role_drives_pool, target_set,
                    role_crit_config,
                )
            if not plan["valid"]:
                reason = str(plan.get("reason") or "").strip()
                if reason:
                    failure_reasons.append(reason)
                continue
            total_score = plan["score"] + tape_score
            total_rank_score = plan.get("rank_score", plan["score"]) + tape_score
            priority_key = tuple(plan.get("stat_priority_key", ()) or ())
            best_priority_key = tuple(best_plan.get("stat_priority_key", ()) or ())
            if (priority_key, total_rank_score, total_score) > (
                best_priority_key,
                best_plan.get("rank_score", best_plan["score"]),
                best_plan["score"],
            ):
                plan["score"] = total_score
                plan["rank_score"] = total_rank_score
                plan["assigned_tape"] = role_tape
                best_plan = plan
    if best_plan["valid"]:
        return best_plan
    assigned_tapes[role_name] = None
    reason = next(
        iter(dict.fromkeys(failure_reasons)),
        "没有可用图纸或无法凑齐图纸所需形状",
    )
    return {"valid": False, "reason": reason}


def _finalize_deferred_slots(
    allocation: dict,
    reservations: DeferredDriveReservationState,
) -> None:
    if not reservations.has_slots:
        return
    for key, drive in reservations.finalize().items():
        slot = reservations.slot(key)
        plan = allocation[slot.role_name]
        field = "assigned_set_drives" if slot.slot_type == "set" else "assigned_extra_drives"
        plan[field][slot.slot_index] = drive
