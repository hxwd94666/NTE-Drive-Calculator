# 角色优先分配策略和平级角色组处理逻辑。
import numpy as np
from scipy.optimize import linear_sum_assignment
from typing import List, Dict

from src.models.equipment import Drive, Tape
from src.optimizer.allocation_matrix_builder import AllocationMatrixBuilder
from src.optimizer.contracts import AllocationResult, CandidatePool, CustomSetMap, StatPriorityConfigMap
from src.solver.blueprint_utils import dedupe_blueprints_by_piece_signature
from src.utils.logger import logger

class RolePriorityStrategy(AllocationMatrixBuilder):
    """Greedy per-role allocation by priority order."""

    def _required_shapes_for_role_blueprints(self, role_name: str, blueprints: list[dict], custom_sets: Dict[str, str]) -> set[str]:
        target_set = self._target_set(role_name, custom_sets)
        shapes = set()
        for blueprint in blueprints:
            shapes.update(self._set_pieces_for_blueprint(blueprint, target_set))
            shapes.update(blueprint.get("extra_pieces", []) or [])
        return shapes

    def _filter_drives_by_shapes(self, drives_pool: list[Drive], required_shapes: set[str]) -> list[Drive]:
        if not required_shapes:
            return []
        return [drive for drive in drives_pool if drive.shape_id in required_shapes]

    def _drive_buckets(self, drives_pool: list[Drive]) -> dict[str, list[tuple[int, Drive]]]:
        buckets = {}
        for index, drive in enumerate(drives_pool):
            buckets.setdefault(drive.shape_id, []).append((index, drive))
        return buckets

    def _find_best_fit(self, role_name: str, blueprint: Dict, available_pool: List[Drive], target_set: str,
                       crit_mode: str | None = None, assigned_tape: Tape | None = None,
                       crit_rate_caps: Dict[str, float] | None = None) -> Dict:
        set_shapes = self._set_pieces_for_blueprint(blueprint, target_set)
        extra_shapes = blueprint["extra_pieces"]
        drive_buckets = self._drive_buckets(available_pool)

        used_indices = set()
        assigned_set, assigned_extra, total_score, total_rank_score = [], [], 0.0, 0.0

        set_uses_bonus = self._slot_uses_extra_shape_bonus("set", blueprint)
        for req_shape in set_shapes:
            candidates = [
                (idx, drive) for idx, drive in drive_buckets.get(req_shape, [])
                if idx not in used_indices
                and self._within_crit_rate_cap(role_name, [assigned_tape, *assigned_set, *assigned_extra, drive], crit_rate_caps)
            ]
            picked = self._pick_best_drive(
                role_name,
                candidates,
                crit_mode,
                include_extra_shape_bonus=set_uses_bonus,
            )
            if picked:
                best_idx, best_drive, highest_score, rank_score = picked
                assigned_set.append(best_drive)
                total_score += highest_score
                total_rank_score += rank_score
                used_indices.add(best_idx)
            else:
                return {"valid": False, "score": 0.0}

        for req_shape in extra_shapes:
            candidates = [
                (idx, drive) for idx, drive in drive_buckets.get(req_shape, [])
                if idx not in used_indices
                and self._within_crit_rate_cap(role_name, [assigned_tape, *assigned_set, *assigned_extra, drive], crit_rate_caps)
            ]
            picked = self._pick_best_drive(
                role_name,
                candidates,
                crit_mode,
                include_extra_shape_bonus=False,
            )
            if picked:
                best_idx, best_drive, highest_score, rank_score = picked
                assigned_extra.append(best_drive)
                total_score += highest_score
                total_rank_score += rank_score
                used_indices.add(best_idx)
            else:
                return {"valid": False, "score": 0.0}

        assigned_drives = assigned_set + assigned_extra
        return {"valid": True, "blueprint": blueprint, "assigned_set_drives": assigned_set,
                "assigned_extra_drives": assigned_extra, "score": round(total_score, 2),
                "rank_score": round(total_rank_score, 2),
                "stat_priority_hits": self._stat_priority_total_hits(role_name, assigned_drives, crit_mode),
                "stat_priority_key": self._stat_priority_key_for_items(role_name, assigned_drives, crit_mode)}

    def _normalize_priority_groups(self, priority_list: List[str], priority_groups: list[list[str]] | None) -> list[list[str]]:
        if not priority_groups:
            return [[role] for role in priority_list]
        selected = [role for role in priority_list if role in self.roles_db]
        seen = set()
        groups = []
        for group in priority_groups:
            clean = []
            for role in group or []:
                if role in selected and role not in seen:
                    clean.append(role)
                    seen.add(role)
            if clean:
                groups.append(clean)
        for role in selected:
            if role not in seen:
                groups.append([role])
        return groups

    def _pre_allocate_tapes_for_groups(
        self,
        priority_groups: list[list[str]],
        custom_sets: Dict[str, str],
        tapes_pool: Dict[str, List[Tape]],
        stat_priority_configs: Dict[str, dict] = None,
    ) -> Dict[str, Tape]:
        assigned_tapes = {}
        used_tape_uids = set()
        stat_priority_configs = stat_priority_configs or {}
        for group in priority_groups:
            if len(group) == 1:
                role = group[0]
                target_set = self._target_set(role, custom_sets)
                best_tape = None
                best_score = -1.0
                for tape in tapes_pool.get(role, []):
                    tape_set = self._resolve_set_name(tape.set_name)
                    if tape_set != tape.set_name and tape_set in self.sets_db:
                        tape.set_name = tape_set
                    if tape.uid in used_tape_uids or tape.set_name != target_set:
                        continue
                    score = tape.role_scores.get(role, 0.0)
                    rank_score = self._rank_score_for_item(role, tape, score, stat_priority_configs.get(role))
                    if rank_score > best_score:
                        best_score, best_tape = rank_score, tape
                assigned_tapes[role] = best_tape
                if best_tape:
                    used_tape_uids.add(best_tape.uid)
                continue

            tapes_by_uid = {}
            role_tape_uids = {}
            for role in group:
                role_tape_uids[role] = {tape.uid for tape in tapes_pool.get(role, [])}
                for tape in tapes_pool.get(role, []):
                    if tape.uid in used_tape_uids:
                        continue
                    resolved_set = self._resolve_set_name(tape.set_name)
                    if resolved_set != tape.set_name and resolved_set in self.sets_db:
                        tape.set_name = resolved_set
                    tapes_by_uid.setdefault(tape.uid, tape)
            real_tapes = list(tapes_by_uid.values())
            for role in group:
                assigned_tapes[role] = None
            if not real_tapes:
                continue

            profit_matrix = np.zeros((len(group), len(real_tapes) + len(group)))
            for r_idx, role in enumerate(group):
                target_set = self._target_set(role, custom_sets)
                for t_idx, tape in enumerate(real_tapes):
                    if tape.uid not in role_tape_uids.get(role, set()) or tape.set_name != target_set:
                        profit_matrix[r_idx, t_idx] = -10000.0
                        continue
                    score = max(0.0, tape.role_scores.get(role, 0.0))
                    rank_score = self._rank_score_for_item(
                        role, tape, score, stat_priority_configs.get(role)
                    )
                    profit_matrix[r_idx, t_idx] = rank_score if rank_score > 0 else 0.000001
            row_ind, col_ind = linear_sum_assignment(-profit_matrix)
            for r_idx, c_idx in zip(row_ind, col_ind):
                if c_idx >= len(real_tapes) or profit_matrix[r_idx, c_idx] < 0:
                    continue
                tape = real_tapes[c_idx]
                assigned_tapes[group[r_idx]] = tape
                used_tape_uids.add(tape.uid)
        return assigned_tapes

    def _dedupe_blueprints_for_role_priority(self, blueprints: list[dict]) -> list[dict]:
        return dedupe_blueprints_by_piece_signature(blueprints)

    def _build_group_profit_matrix(
        self,
        bp_combo: tuple[dict, ...],
        group: list[str],
        drives_pool: list[Drive],
        custom_sets: Dict[str, str],
        crit_priority_modes: Dict[str, dict],
    ):
        slots = []
        for role_idx, role in enumerate(group):
            blueprint = bp_combo[role_idx]
            target_set = self._target_set(role, custom_sets)
            for shape in self._set_pieces_for_blueprint(blueprint, target_set):
                slots.append({"role": role, "type": "set", "shape": shape, "bp": blueprint})
            for shape in blueprint.get("extra_pieces", []):
                slots.append({"role": role, "type": "extra", "shape": shape, "bp": blueprint})
        if len(drives_pool) < len(slots):
            return None, None, None

        profit_matrix = np.zeros((len(slots), len(drives_pool)))
        ranking_matrix = np.zeros((len(slots), len(drives_pool)))
        for slot_idx, slot in enumerate(slots):
            for drive_idx, drive in enumerate(drives_pool):
                if drive.shape_id != slot["shape"]:
                    profit_matrix[slot_idx, drive_idx] = -10000.0
                    ranking_matrix[slot_idx, drive_idx] = -10000.0
                    continue
                score = drive.role_scores.get(slot["role"], 0.0)
                profit_matrix[slot_idx, drive_idx] = score
                slot_uses_bonus = self._slot_uses_extra_shape_bonus(slot["type"], slot.get("bp"))
                ranking_matrix[slot_idx, drive_idx] = self._rank_score_for_drive(
                    slot["role"],
                    drive,
                    score,
                    crit_priority_modes.get(slot["role"]),
                    include_extra_shape_bonus=slot_uses_bonus,
                )
        return slots, profit_matrix, ranking_matrix

    def _init_group_allocation(self, group: list[str], assigned_tapes: Dict[str, Tape]) -> AllocationResult:
        return {
            role: {
                "valid": True,
                "blueprint": None,
                "assigned_tape": assigned_tapes.get(role),
                "assigned_set_drives": [],
                "assigned_extra_drives": [],
                "score": assigned_tapes.get(role).role_scores.get(role, 0.0) if assigned_tapes.get(role) else 0.0,
            }
            for role in group
        }

    def _group_stat_priority_key(self, allocation: AllocationResult, group: list[str], crit_priority_modes: Dict[str, dict]) -> tuple:
        role_entries = []
        for role in group:
            config = crit_priority_modes.get(role)
            if not self._stat_priority_config(config).get("stats"):
                continue
            plan = allocation.get(role, {}) or {}
            items = [
                *(plan.get("assigned_set_drives", []) or []),
                *(plan.get("assigned_extra_drives", []) or []),
            ]
            total_hits = self._stat_priority_total_hits(role, items, config)
            layer_key = self._stat_priority_key_for_items(role, items, config)
            role_entries.append((total_hits, layer_key))
        if not role_entries:
            return ()
        total_hits = sum(entry[0] for entry in role_entries)
        min_hits = min(entry[0] for entry in role_entries)
        sorted_layers = tuple(sorted((entry[1] for entry in role_entries), reverse=True))
        return (total_hits, min_hits, sorted_layers)

    def _group_assignment_key(self, assignments: list[dict], group: list[str], crit_priority_modes: Dict[str, dict]) -> tuple:
        allocation = {
            role: {
                "assigned_set_drives": [],
                "assigned_extra_drives": [],
            }
            for role in group
        }
        score = 0.0
        rank_score = 0.0
        for assignment in assignments:
            slot = assignment["slot"]
            role = slot["role"]
            key = "assigned_set_drives" if slot["type"] == "set" else "assigned_extra_drives"
            allocation.setdefault(role, {"assigned_set_drives": [], "assigned_extra_drives": []})[key].append(
                assignment["drive"]
            )
            score += assignment["profit"]
            rank_score += assignment.get("rank_profit", assignment["profit"])
        return (self._group_stat_priority_key(allocation, group, crit_priority_modes), rank_score, score)

    def _rebalance_group_assignments(
        self,
        assignments: list[dict],
        drives_pool: list[Drive],
        profit_matrix,
        ranking_matrix,
        crit_priority_modes: Dict[str, dict],
        group: list[str],
    ) -> list[dict]:
        if not any(self._stat_priority_config(crit_priority_modes.get(role)).get("stats") for role in group):
            return assignments

        current = [dict(item) for item in assignments]
        improved = True
        while improved:
            improved = False
            best_key = self._group_assignment_key(current, group, crit_priority_modes)
            best_swap = None
            for left in range(len(current)):
                for right in range(left + 1, len(current)):
                    left_slot = current[left]["slot"]
                    right_slot = current[right]["slot"]
                    if left_slot["role"] == right_slot["role"]:
                        continue
                    left_drive = current[left]["drive"]
                    right_drive = current[right]["drive"]
                    if left_slot["shape"] != right_drive.shape_id or right_slot["shape"] != left_drive.shape_id:
                        continue
                    left_new_profit = profit_matrix[current[left]["slot_idx"], current[right]["drive_idx"]]
                    right_new_profit = profit_matrix[current[right]["slot_idx"], current[left]["drive_idx"]]
                    left_new_rank_profit = ranking_matrix[current[left]["slot_idx"], current[right]["drive_idx"]]
                    right_new_rank_profit = ranking_matrix[current[right]["slot_idx"], current[left]["drive_idx"]]
                    if left_new_profit < 0 or right_new_profit < 0:
                        continue
                    candidate = [dict(item) for item in current]
                    candidate[left]["drive_idx"], candidate[right]["drive_idx"] = (
                        candidate[right]["drive_idx"],
                        candidate[left]["drive_idx"],
                    )
                    candidate[left]["drive"] = drives_pool[candidate[left]["drive_idx"]]
                    candidate[right]["drive"] = drives_pool[candidate[right]["drive_idx"]]
                    candidate[left]["profit"] = left_new_profit
                    candidate[right]["profit"] = right_new_profit
                    candidate[left]["rank_profit"] = left_new_rank_profit
                    candidate[right]["rank_profit"] = right_new_rank_profit
                    candidate_key = self._group_assignment_key(candidate, group, crit_priority_modes)
                    if candidate_key > best_key:
                        best_key = candidate_key
                        best_swap = candidate
            if best_swap is not None:
                current = best_swap
                improved = True
        return current

    def _find_best_group_fit(
        self,
        group: list[str],
        drives_pool: list[Drive],
        custom_sets: Dict[str, str],
        assigned_tapes: Dict[str, Tape],
        crit_priority_modes: Dict[str, dict],
        crit_rate_caps: Dict[str, float] | None = None,
    ) -> AllocationResult:
        valid_group = []
        role_blueprints = []
        for role in group:
            blueprints = self._dedupe_blueprints_by_extra_pieces(self.blueprints_db.get(role, []))
            if blueprints:
                valid_group.append(role)
                role_blueprints.append(blueprints)
        if not valid_group:
            return {role: {"valid": False} for role in group}

        best_score = -1.0
        best_rank_score = -1.0
        best_priority_key = ()
        best_allocation = {}
        for bp_combo in self._iter_bp_combos(
            role_blueprints,
            valid_group,
            drives_pool,
            custom_sets,
            crit_priority_modes,
        ):
            slots, profit_matrix, ranking_matrix = self._build_profit_matrix(
                bp_combo, valid_group, drives_pool, custom_sets, crit_priority_modes
            )
            if slots is None:
                continue
            row_ind, col_ind = linear_sum_assignment(-ranking_matrix)
            temp_alloc = self._init_temp_alloc(valid_group, assigned_tapes)
            is_valid = True
            assignments = []
            for slot_idx, drive_idx in zip(row_ind, col_ind):
                profit = profit_matrix[slot_idx, drive_idx]
                if profit < 0:
                    is_valid = False
                    break
                slot = slots[slot_idx]
                drive = drives_pool[drive_idx]
                assignments.append(
                    {
                        "slot_idx": slot_idx,
                        "drive_idx": drive_idx,
                        "slot": slot,
                        "drive": drive,
                        "profit": profit,
                        "rank_profit": ranking_matrix[slot_idx, drive_idx],
                    }
                )
            if not is_valid:
                continue

            assignments = self._rebalance_group_assignments(
                assignments,
                drives_pool,
                profit_matrix,
                ranking_matrix,
                crit_priority_modes,
                valid_group,
            )
            team_score = sum(item["score"] for item in temp_alloc.values())
            team_rank_score = team_score
            for assignment in assignments:
                slot = assignment["slot"]
                drive = assignment["drive"]
                profit = assignment["profit"]
                rank_profit = assignment.get("rank_profit", profit)
                role = slot["role"]
                temp_alloc[role]["blueprint"] = slot["bp"]
                if slot["type"] == "set":
                    temp_alloc[role]["assigned_set_drives"].append(drive)
                else:
                    temp_alloc[role]["assigned_extra_drives"].append(drive)
                temp_alloc[role]["score"] += profit
                team_score += profit
                team_rank_score += rank_profit
            if is_valid:
                for role in valid_group:
                    plan = temp_alloc.get(role, {})
                    items = [
                        plan.get("assigned_tape"),
                        *(plan.get("assigned_set_drives", []) or []),
                        *(plan.get("assigned_extra_drives", []) or []),
                    ]
                    if not self._within_crit_rate_cap(role, items, crit_rate_caps):
                        is_valid = False
                        break
            priority_key = self._group_stat_priority_key(temp_alloc, valid_group, crit_priority_modes)
            if is_valid and (priority_key, team_rank_score, team_score) > (best_priority_key, best_rank_score, best_score):
                best_priority_key = priority_key
                best_rank_score = team_rank_score
                best_score = team_score
                best_allocation = temp_alloc

        for role in group:
            best_allocation.setdefault(role, {"valid": False})
        return best_allocation

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
        assigned_tapes = self._pre_allocate_tapes_for_groups(priority_groups, custom_sets, tapes_pool, crit_priority_modes)
        final_allocation = {}

        for group in priority_groups:
            if len(group) > 1:
                group_allocation = self._find_best_group_fit(
                    group,
                    drives_pool,
                    custom_sets,
                    assigned_tapes,
                    crit_priority_modes,
                    crit_rate_caps,
                )
                final_allocation.update(group_allocation)
                used_uids = set()
                for plan in group_allocation.values():
                    if not plan.get("valid"):
                        continue
                    used_uids.update(d.uid for d in plan.get("assigned_set_drives", []))
                    used_uids.update(d.uid for d in plan.get("assigned_extra_drives", []))
                drives_pool = [d for d in drives_pool if d.uid not in used_uids]
                continue

            role_name = group[0]
            raw_blueprints = self.blueprints_db.get(role_name, [])
            blueprints = self._dedupe_blueprints_for_role_priority(raw_blueprints)
            target_set = self._target_set(role_name, custom_sets)
            required_shapes = self._required_shapes_for_role_blueprints(role_name, blueprints, custom_sets)
            role_drives_pool = self._filter_drives_by_shapes(drives_pool, required_shapes)
            logger.info(f"  [{role_name}] 匹配中... (图纸数: {len(blueprints)}, 候选池: {len(role_drives_pool)})")

            role_tape = assigned_tapes.get(role_name)
            tape_score = role_tape.role_scores.get(role_name, 0.0) if role_tape else 0.0

            best_plan = {"valid": False, "score": -1.0, "rank_score": -1.0, "stat_priority_key": ()}

            for bp in blueprints:
                if crit_rate_caps:
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
                if plan["valid"]:
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
                used_uids = set(d.uid for d in best_plan["assigned_set_drives"]) | set(d.uid for d in best_plan["assigned_extra_drives"])
                drives_pool = [d for d in drives_pool if d.uid not in used_uids]
            else:
                final_allocation[role_name] = {"valid": False}

        return final_allocation

