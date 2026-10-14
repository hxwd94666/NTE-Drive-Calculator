# 实现角色优先组的部分分配、恢复与最终执行策略。
import numpy as np
from typing import List, Dict
from collections import Counter

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
            return {role: {"valid": False, "reason": "角色没有可用图纸"} for role in group}

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
                role, {"valid": False, "reason": "角色没有可用图纸"},
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

        # 求解依赖只随后台匹配加载；冷加载期间取消也不得继续分配。
        self._check_cancelled()
        from scipy.optimize import linear_sum_assignment
        self._check_cancelled()
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
            # 求解依赖只随后台匹配加载；冷加载期间取消也不得继续分配。
            self._check_cancelled()
            from scipy.optimize import linear_sum_assignment
            self._check_cancelled()
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
                plan["reason"] = "角色没有可用图纸"
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
                slot_label = "套装必要" if first["type"] == "set" else "额外"
                plan["valid"] = False
                plan["reason"] = (
                    f"同级组竞争后无剩余候选：缺少 {first['shape']} 驱动"
                    f"（{slot_label}槽位，扩展候选 {matching_count} 个）"
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
        """在固定驱动上联合重选同级卡带；失败分支不保留已转移的 UID。"""

        result = self._copy_allocation(allocation)
        incomplete_roles = {
            slot["role"] for slot in self._missing_slots_for_partial_group(result)
        }
        roles = [role for role in result if role not in incomplete_roles]
        if not roles:
            return result
        options: dict[str, dict[str | None, Tape | None]] = {}
        tapes_by_uid: dict[str, Tape] = {}
        for role in roles:
            self._check_cancelled()
            plan, config = result[role], crit_priority_modes.get(role)
            drives = [
                *(plan.get("assigned_set_drives", ()) or ()),
                *(plan.get("assigned_extra_drives", ()) or ()),
            ]
            primary = plan.get("assigned_tape")
            candidates = [*tapes_pool.get(role, ())]
            if isinstance(primary, Tape):
                candidates.append(primary)
            options[role] = {}
            # 无暴击约束的同级角色也必须参与换卡；固定其旧卡会堵住合法互换。
            # None 是正式缺核心方案，不是失败；仍须满足本角色的暴击上下限。
            for tape in [*candidates, None]:
                if isinstance(tape, Tape) and (
                    tape.uid in used_tape_uids
                    or not self._tape_matches_core_target(role, tape, custom_sets)
                    or not self._repair_quality_allowed(role, tape, config)
                ):
                    continue
                if not self._within_crit_rate_cap(role, [tape, *drives], crit_rate_caps):
                    continue
                if self._crit_floor_failure_reason(role, tape, drives, config):
                    continue
                uid = tape.uid if isinstance(tape, Tape) else None
                options[role][uid] = tape
                if isinstance(tape, Tape):
                    tapes_by_uid.setdefault(uid, tape)

        # 固定驱动后每条合法边只依赖角色/卡带。沿用同级组的二分匹配，
        # 先最大化有效角色数，再沿正式候选排名保留词条优先；不截断 UID 互换。
        tapes = [tapes_by_uid[uid] for uid in sorted(tapes_by_uid)]
        ranks = {
            role: {
                tape.uid: self._rank_score_for_item(
                    role, tape, float(tape.role_scores.get(role, 0.0)), crit_priority_modes.get(role)
                )
                for tape in options[role].values() if isinstance(tape, Tape)
            }
            for role in roles
        }
        success_bonus = 1.0 + sum(
            max((abs(rank) for rank in ranks[role].values()), default=0.0)
            for role in roles
        )
        matrix = np.full((len(roles), len(tapes) + len(roles)), -success_bonus)
        for row, role in enumerate(roles):
            matrix[row, len(tapes) + row] = success_bonus if None in options[role] else 0.0
            for column, tape in enumerate(tapes):
                if tape.uid in options[role]:
                    # 排名只用于选边，后面仍以冻结实分更新结果；同为零时保留可用核心。
                    matrix[row, column] = success_bonus + ranks[role][tape.uid] + 1e-6
        self._check_cancelled()
        from scipy.optimize import linear_sum_assignment
        self._check_cancelled()
        row_indices, column_indices = linear_sum_assignment(-matrix)
        self._check_cancelled()
        selected_uids: set[str] = set()
        for row, column in zip(row_indices.tolist(), column_indices.tolist()):
            role, plan = roles[row], result[roles[row]]
            tape = tapes[column] if column < len(tapes) else None
            previous = plan.get("assigned_tape")
            previous_score = float(previous.role_scores.get(role, 0.0)) if isinstance(previous, Tape) else 0.0
            tape_score = float(tape.role_scores.get(role, 0.0)) if isinstance(tape, Tape) else 0.0
            plan["assigned_tape"] = tape
            plan["score"] = float(plan.get("score", 0.0)) - previous_score + tape_score
            if "rank_score" in plan:
                plan["rank_score"] = float(plan["rank_score"]) - previous_score + tape_score
            plan["valid"] = bool(matrix[row, column] > 0.0)
            if plan["valid"]:
                plan.pop("reason", None)
                if isinstance(tape, Tape):
                    selected_uids.add(tape.uid)

        # 未填满驱动的角色尚未参与上述匹配；其旧卡若已转给成功同级，
        # 补驱动阶段不得凭残留对象再次变为有效。后续约束恢复只用未占用卡带。
        for role in incomplete_roles:
            plan = result[role]
            previous = plan.get("assigned_tape")
            if isinstance(previous, Tape) and previous.uid in selected_uids:
                score = float(previous.role_scores.get(role, 0.0))
                plan["assigned_tape"] = None
                plan["score"] = float(plan.get("score", 0.0)) - score
                if "rank_score" in plan:
                    plan["rank_score"] = float(plan["rank_score"]) - score
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
        """同级组先冻结必需套装件、重选额外槽位，再有界恢复约束失败角色。

        已成功同级角色和前序占用保持固定；只释放失败角色，不借走成功方案 UID。
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
        """Run priority groups through the reservation-aware execution shell."""
        from src.optimizer.role_priority_execution import execute_role_priority

        return execute_role_priority(
            self, candidate_pool, priority_list, custom_sets, crit_priority_modes,
            priority_groups, crit_rate_caps,
        )
