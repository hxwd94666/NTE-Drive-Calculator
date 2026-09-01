# 实现角色优先组的部分分配、恢复与最终执行策略。
import numpy as np
from scipy.optimize import linear_sum_assignment
from typing import List, Dict
from collections import Counter

from src.i18n import tr
from src.models.equipment import Drive, Tape
from src.optimizer.contracts import AllocationResult, CandidatePool, CustomSetMap, StatPriorityConfigMap
from src.utils.logger import logger


class RolePriorityGroupStrategyMixin:
    @staticmethod
    def _allocated_drive_uids(allocation: AllocationResult) -> set[str]:
        """Return only the module UIDs consumed by a successful allocation."""

        used_uids: set[str] = set()
        for plan in allocation.values():
            if not plan.get("valid"):
                continue
            used_uids.update(drive.uid for drive in plan.get("assigned_set_drives", []))
            used_uids.update(drive.uid for drive in plan.get("assigned_extra_drives", []))
        return used_uids

    def _find_best_partial_group_fit(
        self,
        group: list[str],
        drives_pool: list[Drive],
        custom_sets: Dict[str, str],
        assigned_tapes: Dict[str, Tape],
        crit_priority_modes: Dict[str, dict],
    ) -> AllocationResult:
        """Create a fair provisional allocation when a same-priority group is incomplete.

        Set slots receive a higher cardinality priority than extra slots.  This
        gives every equal-priority role one joint first pass before any missing
        slot is expanded from the full inventory.  The resulting assignments
        are intentionally preserved by the second pass.
        """

        valid_group = []
        role_blueprints = []
        for role in group:
            blueprints = self._dedupe_blueprints_by_extra_pieces(
                self.blueprints_db.get(role, [])
            )
            if blueprints:
                valid_group.append(role)
                role_blueprints.append(blueprints)
        if not valid_group:
            return {role: {"valid": False, "reason": tr("角色没有可用图纸")} for role in group}

        best_allocation: AllocationResult = {}
        best_key: tuple = (-1, -1, float("-inf"), float("-inf"))
        for bp_combo in self._iter_bp_combos(
            role_blueprints, valid_group, drives_pool, custom_sets,
            crit_priority_modes,
        ):
            slots = self._build_group_slots(bp_combo, valid_group, custom_sets)
            allocation = self._assign_partial_group_slots(
                slots, drives_pool, assigned_tapes, crit_priority_modes, valid_group,
            )
            set_count = sum(
                len(plan.get("assigned_set_drives", []) or ())
                for plan in allocation.values()
            )
            total_count = sum(
                len(plan.get("assigned_set_drives", []) or ())
                + len(plan.get("assigned_extra_drives", []) or ())
                for plan in allocation.values()
            )
            score = sum(float(plan.get("score", 0.0)) for plan in allocation.values())
            rank_score = sum(float(plan.get("rank_score", plan.get("score", 0.0))) for plan in allocation.values())
            key = (set_count, total_count, rank_score, score)
            if key > best_key:
                best_key = key
                best_allocation = allocation

        for role in group:
            best_allocation.setdefault(
                role, {"valid": False, "reason": tr("角色没有可用图纸")},
            )
        return best_allocation

    def _assign_partial_group_slots(
        self,
        slots: list[dict],
        drives_pool: list[Drive],
        assigned_tapes: Dict[str, Tape],
        crit_priority_modes: Dict[str, dict],
        valid_group: list[str],
    ) -> AllocationResult:
        """Jointly allocate as many slots as possible without stealing later locks."""

        allocation = self._init_group_allocation(valid_group, assigned_tapes)
        for plan in allocation.values():
            plan["rank_score"] = float(plan.get("score", 0.0))
            plan["valid"] = False
        if not slots:
            return allocation

        # Dummy columns make a partial matching explicit.  A set slot is
        # weighted above any extra slot, so temporary extra picks never crowd
        # out an equal-priority role's four-piece requirement.
        invalid = -1_000_000_000.0
        matrix = np.zeros((len(slots), len(drives_pool) + len(slots)))
        matrix[:, :len(drives_pool)] = invalid
        for row_index, slot in enumerate(slots):
            role = slot["role"]
            include_bonus = self._slot_uses_extra_shape_bonus(
                slot["type"], slot.get("bp"),
            )
            slot_bias = 2_000_000.0 if slot["type"] == "set" else 1_000_000.0
            for drive_index, drive in enumerate(drives_pool):
                if (
                    drive.shape_id != slot["shape"]
                    or not self._item_allowed_for_role(
                        drive, crit_priority_modes.get(role)
                    )
                ):
                    continue
                score = float(drive.role_scores.get(role, 0.0))
                rank_score = self._rank_score_for_drive(
                    role, drive, score, crit_priority_modes.get(role),
                    include_extra_shape_bonus=include_bonus,
                )
                matrix[row_index, drive_index] = slot_bias + rank_score

        row_indices, column_indices = linear_sum_assignment(-matrix)
        for row_index, column_index in zip(row_indices.tolist(), column_indices.tolist()):
            if column_index >= len(drives_pool) or matrix[row_index, column_index] <= 0:
                continue
            slot = slots[row_index]
            drive = drives_pool[column_index]
            role = slot["role"]
            plan = allocation[role]
            plan["blueprint"] = slot["bp"]
            score = float(drive.role_scores.get(role, 0.0))
            rank_score = self._rank_score_for_drive(
                role, drive, score, crit_priority_modes.get(role),
                include_extra_shape_bonus=self._slot_uses_extra_shape_bonus(
                    slot["type"], slot.get("bp"),
                ),
            )
            if slot["type"] == "set":
                plan["assigned_set_drives"].append(drive)
            else:
                plan["assigned_extra_drives"].append(drive)
            plan["score"] += score
            plan["rank_score"] += rank_score

        for slot in slots:
            allocation[slot["role"]]["blueprint"] = slot["bp"]
        return allocation

    @staticmethod
    def _copy_allocation(allocation: AllocationResult) -> AllocationResult:
        return {
            role: {
                **plan,
                "assigned_set_drives": list(plan.get("assigned_set_drives", []) or ()),
                "assigned_extra_drives": list(plan.get("assigned_extra_drives", []) or ()),
            }
            for role, plan in allocation.items()
        }

    def _missing_slots_for_partial_group(
        self, allocation: AllocationResult,
    ) -> list[dict]:
        missing: list[dict] = []
        for role, plan in allocation.items():
            blueprint = plan.get("blueprint") or {}
            for slot_type, key in (("set", "set_pieces"), ("extra", "extra_pieces")):
                required = Counter(str(shape) for shape in (blueprint.get(key) or ()))
                assigned = Counter(
                    str(drive.shape_id)
                    for drive in plan.get(
                        "assigned_set_drives" if slot_type == "set" else "assigned_extra_drives", ()
                    ) or ()
                )
                for shape, count in required.items():
                    for _ in range(max(0, count - assigned.get(shape, 0))):
                        missing.append({
                            "role": role,
                            "type": slot_type,
                            "shape": shape,
                            "bp": blueprint,
                        })
        return missing

    def _expanded_top_k_for_missing_slots(
        self,
        missing_slots: list[dict],
        full_drives: list[Drive],
        available_uids: set[str],
        frozen_uids: set[str],
        crit_priority_modes: Dict[str, dict],
        candidate_limit: int,
    ) -> list[Drive]:
        """Re-screen only missing slots from the full fixed inventory snapshot."""

        selected: dict[str, Drive] = {}
        seen_requirements: set[tuple[str, str, str]] = set()
        for slot in missing_slots:
            requirement = (slot["role"], slot["type"], slot["shape"])
            if requirement in seen_requirements:
                continue
            seen_requirements.add(requirement)
            role = slot["role"]
            include_bonus = self._slot_uses_extra_shape_bonus(
                slot["type"], slot.get("bp"),
            )
            candidates = [
                drive for drive in full_drives
                if drive.uid in available_uids
                and drive.uid not in frozen_uids
                and drive.shape_id == slot["shape"]
                and self._item_allowed_for_role(
                    drive, crit_priority_modes.get(role)
                )
            ]
            candidates.sort(
                key=lambda drive: self._rank_score_for_drive(
                    role,
                    drive,
                    float(drive.role_scores.get(role, 0.0)),
                    crit_priority_modes.get(role),
                    include_extra_shape_bonus=include_bonus,
                ),
                reverse=True,
            )
            for drive in candidates[:candidate_limit]:
                selected.setdefault(drive.uid, drive)
        return list(selected.values())

    def _complete_partial_group_fit(
        self,
        allocation: AllocationResult,
        full_drives: list[Drive],
        available_uids: set[str],
        crit_priority_modes: Dict[str, dict],
        crit_rate_caps: Dict[str, float] | None,
        candidate_limit: int,
    ) -> AllocationResult:
        """Freeze first-pass required-set drives and rebuild every extra slot."""

        result = self._copy_allocation(allocation)
        # Only blueprint-required set pieces survive the first freeze.  Extra
        # pieces must return to the full fixed snapshot so critical-rate repair
        # can choose a different extra-shape implementation without disturbing
        # the required four-piece core.
        for role, plan in result.items():
            released_extra = list(plan.get("assigned_extra_drives", []) or ())
            plan["assigned_extra_drives"] = []
            plan["score"] = float(plan.get("score", 0.0)) - sum(
                float(drive.role_scores.get(role, 0.0)) for drive in released_extra
            )
            tape = plan.get("assigned_tape")
            tape_score = float(tape.role_scores.get(role, 0.0)) if isinstance(tape, Tape) else 0.0
            plan["rank_score"] = tape_score + sum(
                self._rank_score_for_drive(
                    role,
                    drive,
                    float(drive.role_scores.get(role, 0.0)),
                    crit_priority_modes.get(role),
                    include_extra_shape_bonus=self._slot_uses_extra_shape_bonus(
                        "set", plan.get("blueprint"),
                    ),
                )
                for drive in plan.get("assigned_set_drives", ()) or ()
            )
        frozen_uids = {
            drive.uid
            for plan in result.values()
            for drive in plan.get("assigned_set_drives", ()) or ()
        }
        missing_slots = self._missing_slots_for_partial_group(result)
        candidates = self._expanded_top_k_for_missing_slots(
            missing_slots, full_drives, available_uids, frozen_uids,
            crit_priority_modes, candidate_limit,
        )
        if missing_slots:
            invalid = -1_000_000_000.0
            # Dummy columns preserve a maximal partial assignment when the
            # released extra slots outnumber available candidates.  Without
            # them, one impossible peer would invalidate every otherwise
            # complete same-priority role.
            column_count = len(candidates) + len(missing_slots)
            profit_matrix = np.zeros((len(missing_slots), column_count))
            rank_matrix = np.zeros((len(missing_slots), column_count))
            profit_matrix[:, :len(candidates)] = invalid
            rank_matrix[:, :len(candidates)] = invalid
            for row_index, slot in enumerate(missing_slots):
                role = slot["role"]
                include_bonus = self._slot_uses_extra_shape_bonus(
                    slot["type"], slot.get("bp"),
                )
                for column_index, drive in enumerate(candidates):
                    if (
                        drive.shape_id != slot["shape"]
                        or not self._item_allowed_for_role(
                            drive, crit_priority_modes.get(role)
                        )
                    ):
                        continue
                    score = float(drive.role_scores.get(role, 0.0))
                    profit_matrix[row_index, column_index] = score
                    slot_bias = 2_000_000.0 if slot["type"] == "set" else 1_000_000.0
                    rank_matrix[row_index, column_index] = slot_bias + self._rank_score_for_drive(
                        role,
                        drive,
                        score,
                        crit_priority_modes.get(role),
                        include_extra_shape_bonus=include_bonus,
                    )
            row_indices, column_indices = linear_sum_assignment(-rank_matrix)
            for row_index, column_index in zip(row_indices.tolist(), column_indices.tolist()):
                if (
                    column_index >= len(candidates)
                    or rank_matrix[row_index, column_index] <= 0
                ):
                    continue
                slot = missing_slots[row_index]
                drive = candidates[column_index]
                plan = result[slot["role"]]
                if slot["type"] == "set":
                    plan["assigned_set_drives"].append(drive)
                else:
                    plan["assigned_extra_drives"].append(drive)
                plan["score"] += float(profit_matrix[row_index, column_index])
                slot_bias = 2_000_000.0 if slot["type"] == "set" else 1_000_000.0
                plan["rank_score"] = float(plan.get("rank_score", plan["score"])) + float(
                    rank_matrix[row_index, column_index] - slot_bias
                )

        remaining = self._missing_slots_for_partial_group(result)
        return self._mark_partial_group_failures(
            result,
            remaining,
            candidates,
            crit_rate_caps,
            crit_priority_modes,
        )

    def _mark_partial_group_failures(
        self,
        result: AllocationResult,
        missing_slots: list[dict],
        candidates: list[Drive],
        crit_rate_caps: Dict[str, float] | None = None,
        crit_priority_modes: Dict[str, dict] | None = None,
    ) -> AllocationResult:
        crit_priority_modes = crit_priority_modes or {}
        missing_by_role: dict[str, list[dict]] = {}
        for slot in missing_slots:
            missing_by_role.setdefault(slot["role"], []).append(slot)
        for role, plan in result.items():
            if not plan.get("blueprint"):
                plan["valid"] = False
                plan["reason"] = tr("角色没有可用图纸")
                continue
            items = [
                plan.get("assigned_tape"),
                *(plan.get("assigned_set_drives", []) or ()),
                *(plan.get("assigned_extra_drives", []) or ()),
            ]
            role_missing = missing_by_role.get(role, [])
            if role_missing:
                first = role_missing[0]
                matching_count = sum(
                    1 for drive in candidates if drive.shape_id == first["shape"]
                )
                slot_label = tr("套装必要") if first["type"] == "set" else tr("额外")
                plan["valid"] = False
                plan["reason"] = (
                    tr("同级组竞争后无剩余候选：缺少 {shape} 驱动"
                       "（{slot}槽位，扩展候选 {count} 个）",
                       shape=first["shape"], slot=slot_label, count=matching_count)
                )
            elif not self._within_crit_rate_cap(role, items, crit_rate_caps):
                cap = self._crit_rate_cap(role, crit_rate_caps)
                plan["valid"] = False
                plan["reason"] = f"暴击率上限 {cap:g}% 使冻结后的同级组方案无法成立"
            elif floor_failure := self._crit_floor_failure_reason(
                role,
                plan.get("assigned_tape"),
                [
                    *(plan.get("assigned_set_drives", []) or []),
                    *(plan.get("assigned_extra_drives", []) or []),
                ],
                crit_priority_modes.get(role),
            ):
                plan["valid"] = False
                plan["reason"] = floor_failure
            else:
                plan["valid"] = True
                plan.pop("reason", None)
                plan.pop("rank_score", None)
        return result

    def _retry_complete_group_tapes(
        self,
        allocation: AllocationResult,
        custom_sets: Dict[str, str],
        tapes_pool: dict[str, list[Tape]],
        used_tape_uids: set[str],
        crit_priority_modes: Dict[str, dict],
        crit_rate_caps: Dict[str, float] | None,
    ) -> AllocationResult:
        """Try card-only constraint repair before releasing any drive."""

        result = self._copy_allocation(allocation)
        incomplete_roles = {
            slot["role"] for slot in self._missing_slots_for_partial_group(result)
        }
        states = [{"tapes": {}, "uids": set(used_tape_uids), "valid": 0, "score": 0.0}]
        for role, plan in result.items():
            if role in incomplete_roles:
                continue
            config = crit_priority_modes.get(role)
            constrained = (
                self._crit_floor_threshold(config) is not None
                or self._crit_rate_cap(role, crit_rate_caps) is not None
            )
            primary = plan.get("assigned_tape")
            legal: list[Tape | None] = []
            if constrained:
                candidates = [
                    tape
                    for tape in tapes_pool.get(role, ())
                    if tape.uid not in used_tape_uids
                    and self._tape_matches_core_target(role, tape, custom_sets)
                    and self._repair_quality_allowed(role, tape, config)
                ]
                if (
                    isinstance(primary, Tape)
                    and primary.uid not in used_tape_uids
                    and self._repair_quality_allowed(role, primary, config)
                    and all(tape.uid != primary.uid for tape in candidates)
                ):
                    candidates.append(primary)
                candidates.sort(
                    key=lambda tape: float(tape.role_scores.get(role, 0.0)),
                    reverse=True,
                )
                selected: dict[str, Tape] = {}
                if isinstance(primary, Tape):
                    selected[primary.uid] = primary
                for candidate in (
                    next((t for t in candidates if self._is_crit_rate_key(t.main_stats)), None),
                    next((t for t in candidates if not self._is_crit_rate_key(t.main_stats)), None),
                ):
                    if isinstance(candidate, Tape):
                        selected.setdefault(candidate.uid, candidate)
                for candidate in candidates:
                    selected.setdefault(candidate.uid, candidate)
                    if len(selected) >= 6:
                        break
                legal = list(selected.values())
            else:
                legal = [primary] if isinstance(primary, Tape) else [None]
            if not legal and primary is None:
                legal = [None]

            drives = [
                *(plan.get("assigned_set_drives", ()) or ()),
                *(plan.get("assigned_extra_drives", ()) or ()),
            ]
            valid_options: list[Tape | None] = []
            for tape in legal:
                items = [tape, *drives]
                if not self._within_crit_rate_cap(role, items, crit_rate_caps):
                    continue
                if self._crit_floor_failure_reason(role, tape, drives, config):
                    continue
                valid_options.append(tape)

            next_states: list[dict] = []
            for state in states:
                for tape in valid_options:
                    tape_uid = tape.uid if isinstance(tape, Tape) else None
                    if tape_uid is not None and tape_uid in state["uids"]:
                        continue
                    tape_score = float(tape.role_scores.get(role, 0.0)) if isinstance(tape, Tape) else 0.0
                    next_states.append(
                        {
                            "tapes": {**state["tapes"], role: tape},
                            "uids": state["uids"] | ({tape_uid} if tape_uid else set()),
                            "valid": state["valid"] + 1,
                            "score": state["score"] + tape_score,
                        }
                    )
                next_states.append(
                    {
                        **state,
                        "tapes": {**state["tapes"], role: False},
                    }
                )
            next_states.sort(key=lambda state: (state["valid"], state["score"]), reverse=True)
            states = next_states[:64]

        chosen = states[0]["tapes"] if states else {}
        for role, tape in chosen.items():
            plan = result[role]
            if tape is False:
                plan["valid"] = False
                continue
            previous = plan.get("assigned_tape")
            previous_score = float(previous.role_scores.get(role, 0.0)) if isinstance(previous, Tape) else 0.0
            tape_score = float(tape.role_scores.get(role, 0.0)) if isinstance(tape, Tape) else 0.0
            plan["assigned_tape"] = tape
            plan["score"] = float(plan.get("score", 0.0)) - previous_score + tape_score
            plan["valid"] = True
            plan.pop("reason", None)
        return result

    def _recover_equal_priority_group(
        self,
        group: list[str],
        drives_pool: list[Drive],
        custom_sets: Dict[str, str],
        assigned_tapes: Dict[str, Tape],
        crit_priority_modes: Dict[str, dict],
        crit_rate_caps: Dict[str, float] | None,
        full_drives: list[Drive] | None = None,
        occupied_uids: set[str] | None = None,
        tapes_pool: dict[str, list[Tape]] | None = None,
        used_tape_uids: set[str] | None = None,
        candidate_limit: int = 15,
    ) -> AllocationResult:
        """Repair a failed same-priority group in two bounded stages.

        The first retry freezes only blueprint-required set drives and rebuilds
        every extra slot.  Constraint failures then release only the failed
        roles and traverse all semantic blueprints; successful peers stay fixed.
        """

        provisional = self._find_best_partial_group_fit(
            group, drives_pool, custom_sets, assigned_tapes, crit_priority_modes,
        )
        full_pool = list(full_drives or drives_pool)
        occupied_uids = set(occupied_uids or ())
        tape_stage = self._retry_complete_group_tapes(
            provisional,
            custom_sets,
            tapes_pool or {},
            set(used_tape_uids or ()),
            crit_priority_modes,
            crit_rate_caps,
        )
        tape_stage_valid = {
            role: plan for role, plan in tape_stage.items() if plan.get("valid")
        }
        remaining = {
            role: plan for role, plan in tape_stage.items() if not plan.get("valid")
        }
        if remaining:
            protected_uids = self._allocated_drive_uids(tape_stage_valid)
            completed = self._complete_partial_group_fit(
                remaining,
                full_pool,
                {
                    drive.uid
                    for drive in full_pool
                    if drive.uid not in occupied_uids and drive.uid not in protected_uids
                },
                crit_priority_modes,
                crit_rate_caps,
                max(1, int(candidate_limit)),
            )
            recovered = {**tape_stage_valid, **completed}
        else:
            recovered = tape_stage
        constrained_failures = [
            role
            for role in group
            if not recovered.get(role, {}).get("valid")
            and (
                self._crit_floor_threshold(crit_priority_modes.get(role)) is not None
                or self._crit_rate_cap(role, crit_rate_caps) is not None
            )
        ]
        if constrained_failures:
            valid_peer_drive_uids = self._allocated_drive_uids(recovered)
            valid_peer_tape_uids = {
                tape.uid
                for role, plan in recovered.items()
                if role not in constrained_failures and plan.get("valid")
                for tape in [plan.get("assigned_tape")]
                if isinstance(tape, Tape)
            }
            repaired = self._repair_failed_equal_priority_roles(
                constrained_failures,
                full_pool,
                {
                    drive.uid
                    for drive in full_pool
                    if drive.uid not in occupied_uids
                    and drive.uid not in valid_peer_drive_uids
                },
                tapes_pool or {},
                assigned_tapes,
                set(used_tape_uids or ()) | valid_peer_tape_uids,
                custom_sets,
                crit_priority_modes,
                crit_rate_caps,
                max(1, int(candidate_limit)),
            )
            recovered.update(repaired)
        logger.info(
            "同级组联合匹配未完整：首轮仅冻结图纸必需套装驱动并重选额外驱动；"
            "仍受暴击约束阻断的角色已从零遍历全部语义图纸。"
        )
        return recovered

    def execute(self, candidate_pool: CandidatePool, priority_list: List[str], custom_sets: CustomSetMap,
                crit_priority_modes: StatPriorityConfigMap = None,
                priority_groups: list[list[str]] | None = None,
                crit_rate_caps: Dict[str, float] | None = None) -> AllocationResult:
        logger.info("启动分配模式: 角色优先")

        drives_pool = list(candidate_pool.get("drives", []))
        tapes_pool = candidate_pool.get("tapes", {})
        crit_priority_modes = crit_priority_modes or {}
        crit_rate_caps = crit_rate_caps or {}
        priority_groups = self._normalize_priority_groups(priority_list, priority_groups)
        assigned_tapes = {}
        final_allocation = {}
        used_tape_uids: set[str] = set()
        occupied_drive_uids: set[str] = set()

        for group in priority_groups:
            tape_eligible_group = [
                role for role in group
                if self.blueprints_db.get(role)
            ]
            available_tapes = {
                role: [
                    tape
                    for tape in tapes_pool.get(role, [])
                    if tape.uid not in used_tape_uids
                ]
                for role in tape_eligible_group
            }
            current_tapes = self._pre_allocate_tapes_for_groups(
                [tape_eligible_group] if tape_eligible_group else [],
                custom_sets,
                available_tapes,
                crit_priority_modes,
            )
            for role in group:
                assigned_tapes[role] = current_tapes.get(role)

            if len(group) > 1:
                group_allocation = self._find_best_group_fit(
                    group,
                    drives_pool,
                    custom_sets,
                    assigned_tapes,
                    crit_priority_modes,
                    crit_rate_caps,
                )
                # 同级组首轮始终联合分配。第一次恢复只冻结图纸必需
                # 套装驱动，额外驱动全部回到固定快照重选；仍受暴击约束
                # 阻断的角色才从零遍历全部语义图纸，成功同级角色不重算。
                failed_roles = [
                    role for role in group
                    if not group_allocation.get(role, {}).get("valid")
                ]
                if failed_roles:
                    group_allocation = self._recover_equal_priority_group(
                        group, drives_pool, custom_sets, assigned_tapes,
                        crit_priority_modes, crit_rate_caps,
                        full_drives=list(candidate_pool.get("all_drives") or drives_pool),
                        occupied_uids=occupied_drive_uids,
                        tapes_pool=tapes_pool,
                        used_tape_uids=used_tape_uids,
                        candidate_limit=int(candidate_pool.get("drive_screen_limit") or 15),
                    )
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
                used_uids = self._allocated_drive_uids(group_allocation)
                occupied_drive_uids.update(used_uids)
                drives_pool = [d for d in drives_pool if d.uid not in used_uids]
                continue

            role_name = group[0]
            raw_blueprints = self.blueprints_db.get(role_name, [])
            blueprints = self._dedupe_blueprints_for_role_priority(raw_blueprints)
            target_set = self._target_set(role_name, custom_sets)
            required_shapes = self._required_shapes_for_role_blueprints(role_name, blueprints, custom_sets)
            role_drives_pool = self._filter_drives_by_shapes(drives_pool, required_shapes)
            logger.info(f"  [{role_name}] 匹配中... (图纸数: {len(blueprints)}, 候选池: {len(role_drives_pool)})")

            best_plan = {"valid": False, "score": -1.0, "rank_score": -1.0, "stat_priority_key": ()}
            failure_reasons: list[str] = []
            role_crit_config = crit_priority_modes.get(role_name)
            retry_tape_candidates = (
                self._crit_rate_cap(role_name, crit_rate_caps) is not None
                or self._crit_floor_threshold(role_crit_config) is not None
            )
            tape_candidates = (
                self._tape_candidates_for_capped_role(
                    role_name, assigned_tapes, tapes_pool, used_tape_uids, custom_sets,
                    role_crit_config,
                )
                if retry_tape_candidates
                else [assigned_tapes.get(role_name)]
            )

            for bp in blueprints:
                for role_tape in tape_candidates:
                    tape_score = role_tape.role_scores.get(role_name, 0.0) if role_tape else 0.0
                    # Keep the long-standing non-cap call shape intact:
                    # extensions/tests may override this method with the
                    # historic five-argument signature.
                    if retry_tape_candidates:
                        plan = self._find_best_fit(
                            role_name,
                            bp,
                            role_drives_pool,
                            target_set,
                            crit_priority_modes.get(role_name),
                            role_tape,
                            crit_rate_caps,
                        )
                    else:
                        plan = self._find_best_fit(
                            role_name,
                            bp,
                            role_drives_pool,
                            target_set,
                            crit_priority_modes.get(role_name),
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
                best_plan.pop("rank_score", None)
                final_allocation[role_name] = best_plan
                if isinstance(best_plan.get("assigned_tape"), Tape):
                    used_tape_uids.add(best_plan["assigned_tape"].uid)
                used_uids = set(d.uid for d in best_plan["assigned_set_drives"]) | set(d.uid for d in best_plan["assigned_extra_drives"])
                occupied_drive_uids.update(used_uids)
                drives_pool = [d for d in drives_pool if d.uid not in used_uids]
            else:
                assigned_tapes[role_name] = None
                reason = next(iter(dict.fromkeys(failure_reasons)), "没有可用图纸或无法凑齐图纸所需形状")
                final_allocation[role_name] = {"valid": False, "reason": reason}

        return final_allocation
