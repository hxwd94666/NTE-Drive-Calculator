# 实现角色优先、驱动优先和全局最优分配策略。
"""Allocation strategies for role-first, item-first, and global matching."""

import copy
import heapq
import itertools
import numpy as np
from scipy.optimize import linear_sum_assignment
from typing import List, Dict, Any

from src.utils.logger import logger
from src.utils.name_resolver import resolve_name
from src.models.equipment import Drive, Tape
from src.optimizer.contracts import AllocationResult, CandidatePool, CustomSetMap, StatPriorityConfigMap

class BaseDispatchStrategy:
    MAX_COMBO_LIMIT = 500

    def __init__(self, roles_db: Dict, sets_db: Dict, blueprints_db: Dict[str, List[Dict]]):
        self.roles_db = roles_db
        self.sets_db = sets_db
        self.blueprints_db = blueprints_db

    def _resolve_set_name(self, set_name: str) -> str:
        resolved = resolve_name(set_name, self.sets_db.keys(), cutoff=0.78)
        return resolved or set_name

    def _target_set(self, role: str, custom_sets: Dict[str, str]) -> str:
        raw_set = (custom_sets or {}).get(role, self.roles_db[role]["default_set"])
        target_set = self._resolve_set_name(raw_set)
        if target_set not in self.sets_db:
            raise ValueError(f"错误：指定的套装 {raw_set} 不存在于 sets.json 中！")
        return target_set

    def _stat_priority_config(self, config) -> dict:
        if not isinstance(config, dict):
            return {}
        stats = [str(s) for s in config.get("stats", []) if s]
        if not stats:
            return {}
        return {
            "stats": stats,
            "equal_priority": bool(config.get("equal_priority", False)),
            "ignore_grade_limit": bool(config.get("ignore_grade_limit", False)),
        }

    def _item_has_stat(self, item, stat_key: str) -> bool:
        target = str(stat_key or "").replace("%", "")
        names = [str(name).replace("%", "") for name in (getattr(item, "sub_stats", {}) or {}).keys()]
        return any(target == name or target in name or name in target for name in names)

    def _covered_stat_count(self, item, stats: list[str]) -> int:
        return sum(1 for stat_key in stats if self._item_has_stat(item, stat_key))

    def _is_a_grade_item(self, role: str, item) -> bool:
        score = getattr(item, "role_scores", {}).get(role, 0.0)
        area = getattr(item, "area", 1) or 1
        return score >= area * 10.0 * 0.4

    def _rank_score_for_item(self, role: str, item, base_score: float, config) -> float:
        if base_score < 0:
            return base_score
        cfg = self._stat_priority_config(config)
        stats = cfg.get("stats", [])
        if not stats or (not cfg.get("ignore_grade_limit") and not self._is_a_grade_item(role, item)):
            return base_score
        if cfg.get("equal_priority"):
            covered = self._covered_stat_count(item, stats)
            return base_score + covered * 100000.0 if covered else base_score
        for tier, stat_key in enumerate(stats):
            if self._item_has_stat(item, stat_key):
                return base_score + (len(stats) - tier) * 100000.0
        return base_score

    def _rank_score_for_drive(self, role: str, drive: Drive, base_score: float, config) -> float:
        return self._rank_score_for_item(role, drive, base_score, config)

    def _set_pieces_for_blueprint(self, blueprint: Dict, target_set: str) -> list[str]:
        if "set_pieces" in blueprint:
            return list(blueprint.get("set_pieces") or [])
        return list(self.sets_db[target_set]["shapes"])

    def _pick_best_drive(self, role: str, candidates: list[tuple[int, Drive]], config=None) -> tuple[int, Drive, float] | None:
        if not candidates:
            return None
        ranked = [
            (self._rank_score_for_drive(role, drive, drive.role_scores.get(role, 0.0), config), idx, drive)
            for idx, drive in candidates
        ]
        _, idx, drive = max(ranked, key=lambda item: item[0])
        return idx, drive, drive.role_scores.get(role, 0.0)

    def _pre_allocate_tapes(self, priority_list: List[str], custom_sets: Dict[str, str],
                            tapes_pool: Dict[str, List[Tape]], stat_priority_configs: Dict[str, dict] = None) -> Dict[str, Tape]:
        assigned_tapes = {}
        used_tape_uids = set()
        stat_priority_configs = stat_priority_configs or {}

        for role in priority_list:
            target_set = self._target_set(role, custom_sets)
            role_tapes = tapes_pool.get(role, [])

            best_tape = None
            best_score = -1.0

            for tape in role_tapes:
                tape_set = self._resolve_set_name(tape.set_name)
                if tape_set != tape.set_name and tape_set in self.sets_db:
                    tape.set_name = tape_set
                if tape.uid not in used_tape_uids and tape.set_name == target_set:
                    score = tape.role_scores.get(role, 0.0)
                    rank_score = self._rank_score_for_item(role, tape, score, stat_priority_configs.get(role))
                    if rank_score > best_score:
                        best_score, best_tape = rank_score, tape

            if best_tape:
                assigned_tapes[role] = best_tape
                used_tape_uids.add(best_tape.uid)
            else:
                assigned_tapes[role] = None

        return assigned_tapes

    def _pre_allocate_tapes_optimal(self, priority_list: List[str], custom_sets: Dict[str, str],
                                    tapes_pool: Dict[str, List[Tape]], stat_priority_configs: Dict[str, dict] = None) -> Dict[str, Tape]:
        """Maximize tape score across all selected roles while keeping one tape per role."""
        assigned_tapes = {role: None for role in priority_list}
        stat_priority_configs = stat_priority_configs or {}
        if not priority_list:
            return assigned_tapes

        tapes_by_uid = {}
        for role_tapes in tapes_pool.values():
            for tape in role_tapes:
                resolved_set = self._resolve_set_name(tape.set_name)
                if resolved_set != tape.set_name and resolved_set in self.sets_db:
                    tape.set_name = resolved_set
                tapes_by_uid.setdefault(tape.uid, tape)

        real_tapes = list(tapes_by_uid.values())
        if not real_tapes:
            return assigned_tapes

        dummy_count = len(priority_list)
        profit_matrix = np.zeros((len(priority_list), len(real_tapes) + dummy_count))

        for r_idx, role in enumerate(priority_list):
            target_set = self._target_set(role, custom_sets)
            for t_idx, tape in enumerate(real_tapes):
                if tape.set_name == target_set:
                    score = max(0.0, tape.role_scores.get(role, 0.0))
                    profit_matrix[r_idx, t_idx] = self._rank_score_for_item(role, tape, score, stat_priority_configs.get(role))
                else:
                    profit_matrix[r_idx, t_idx] = -10000.0

        row_ind, col_ind = linear_sum_assignment(-profit_matrix)
        for r_idx, c_idx in zip(row_ind, col_ind):
            if c_idx >= len(real_tapes):
                continue
            score = profit_matrix[r_idx, c_idx]
            if score > 0:
                assigned_tapes[priority_list[r_idx]] = real_tapes[c_idx]

        return assigned_tapes

    def _blueprint_extra_key(self, blueprint):
        set_key = tuple(sorted(str(shape_id) for shape_id in blueprint.get("set_pieces", [])))
        extra_key = tuple(sorted(str(shape_id) for shape_id in blueprint.get("extra_pieces", [])))
        return set_key, extra_key

    def _dedupe_blueprints_by_extra_pieces(self, blueprints):
        seen = set()
        unique = []
        for bp in blueprints:
            key = self._blueprint_extra_key(bp)
            if key in seen:
                continue
            seen.add(key)
            unique.append(bp)
        return unique

    def _shape_score_buckets(self, role, drives_pool, crit_config=None):
        buckets = {}
        for drive in drives_pool or []:
            base_score = drive.role_scores.get(role, 0.0)
            rank_score = self._rank_score_for_drive(role, drive, base_score, crit_config)
            buckets.setdefault(drive.shape_id, []).append(rank_score)
        for scores in buckets.values():
            scores.sort(reverse=True)
        return buckets

    def _blueprint_theoretical_score(self, role, blueprint, drives_pool, custom_sets, crit_config=None):
        target_set = self._target_set(role, custom_sets)
        required_shapes = self._set_pieces_for_blueprint(blueprint, target_set) + list(blueprint.get("extra_pieces", []))
        buckets = self._shape_score_buckets(role, drives_pool, crit_config)
        used_counts = {}
        total = 0.0
        for shape in required_shapes:
            used = used_counts.get(shape, 0)
            scores = buckets.get(shape, [])
            if used >= len(scores):
                return -10000.0
            total += scores[used]
            used_counts[shape] = used + 1
        return total

    def _rank_role_blueprints(self, role_bps_list, valid_roles, drives_pool, custom_sets, crit_priority_modes=None):
        crit_priority_modes = crit_priority_modes or {}
        ranked = []
        for role, bps in zip(valid_roles, role_bps_list):
            role_ranked = [
                (
                    self._blueprint_theoretical_score(
                        role,
                        bp,
                        drives_pool,
                        custom_sets,
                        crit_priority_modes.get(role),
                    ),
                    index,
                    bp,
                )
                for index, bp in enumerate(bps)
            ]
            role_ranked.sort(key=lambda item: (-item[0], item[1]))
            ranked.append(role_ranked)
        return ranked

    def _iter_ranked_bp_combos(self, ranked_role_bps):
        if not ranked_role_bps or any(not bps for bps in ranked_role_bps):
            return

        start = tuple(0 for _ in ranked_role_bps)

        def score_for(indexes):
            return sum(ranked_role_bps[role_idx][bp_idx][0] for role_idx, bp_idx in enumerate(indexes))

        seen = {start}
        heap = [(-score_for(start), start)]
        count = 0

        while heap and count < self.MAX_COMBO_LIMIT:
            _, indexes = heapq.heappop(heap)
            yield tuple(ranked_role_bps[role_idx][bp_idx][2] for role_idx, bp_idx in enumerate(indexes))
            count += 1

            for role_idx in range(len(indexes)):
                next_indexes = list(indexes)
                next_indexes[role_idx] += 1
                if next_indexes[role_idx] >= len(ranked_role_bps[role_idx]):
                    continue
                next_indexes = tuple(next_indexes)
                if next_indexes in seen:
                    continue
                seen.add(next_indexes)
                heapq.heappush(heap, (-score_for(next_indexes), next_indexes))

    def _iter_bp_combos(self, role_bps_list, valid_roles=None, drives_pool=None, custom_sets=None, crit_priority_modes=None):
        total = 1
        for bps in role_bps_list:
            total *= len(bps)
        if total <= self.MAX_COMBO_LIMIT:
            yield from itertools.product(*role_bps_list)
        else:
            logger.info(f"图纸组合数 {total} 过大，按理论上限筛选前 {self.MAX_COMBO_LIMIT} 组...")
            if not valid_roles or drives_pool is None:
                count = 0
                for combo in itertools.product(*role_bps_list):
                    yield combo
                    count += 1
                    if count >= self.MAX_COMBO_LIMIT:
                        break
                return

            ranked_role_bps = self._rank_role_blueprints(
                role_bps_list,
                valid_roles,
                drives_pool,
                custom_sets or {},
                crit_priority_modes,
            )
            yield from self._iter_ranked_bp_combos(ranked_role_bps)

    def _build_profit_matrix(self, bp_combo, valid_roles, drives_pool, custom_sets, crit_priority_modes=None):
        crit_priority_modes = crit_priority_modes or {}
        slots = []
        for role_idx, role in enumerate(valid_roles):
            bp = bp_combo[role_idx]
            target_set = self._target_set(role, custom_sets)
            for shape in self._set_pieces_for_blueprint(bp, target_set):
                slots.append({"role": role, "type": "set", "shape": shape, "set_name": target_set, "bp": bp})
            for shape in bp["extra_pieces"]:
                slots.append({"role": role, "type": "extra", "shape": shape, "set_name": None, "bp": bp})

        if len(drives_pool) < len(slots): return None, None, None

        profit_matrix = np.zeros((len(slots), len(drives_pool)))
        ranking_matrix = np.zeros((len(slots), len(drives_pool)))
        for i, slot in enumerate(slots):
            for j, drive in enumerate(drives_pool):
                if drive.shape_id != slot["shape"]:
                    profit_matrix[i, j] = -10000.0
                    ranking_matrix[i, j] = -10000.0
                else:
                    score = drive.role_scores.get(slot["role"], 0.0)
                    profit_matrix[i, j] = score
                    ranking_matrix[i, j] = self._rank_score_for_drive(
                        slot["role"], drive, score, crit_priority_modes.get(slot["role"])
                    )

        return slots, profit_matrix, ranking_matrix

    def _init_temp_alloc(self, valid_roles, assigned_tapes):
        return {r: {
            "valid": True,
            "blueprint": None,
            "assigned_tape": assigned_tapes.get(r),
            "assigned_set_drives": [],
            "assigned_extra_drives": [],
            "score": assigned_tapes.get(r).role_scores.get(r, 0.0) if assigned_tapes.get(r) else 0.0
        } for r in valid_roles}

    def execute(self, candidate_pool: CandidatePool, priority_list: List[str], custom_sets: CustomSetMap,
                crit_priority_modes: StatPriorityConfigMap = None,
                priority_groups: list[list[str]] | None = None) -> AllocationResult:
        raise NotImplementedError

class RolePriorityStrategy(BaseDispatchStrategy):
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
                       crit_mode: str | None = None) -> Dict:
        set_shapes = self._set_pieces_for_blueprint(blueprint, target_set)
        extra_shapes = blueprint["extra_pieces"]
        drive_buckets = self._drive_buckets(available_pool)

        used_indices = set()
        assigned_set, assigned_extra, total_score = [], [], 0.0

        for req_shape in set_shapes:
            candidates = [
                (idx, drive) for idx, drive in drive_buckets.get(req_shape, [])
                if idx not in used_indices
            ]
            picked = self._pick_best_drive(role_name, candidates, crit_mode)
            if picked:
                best_idx, best_drive, highest_score = picked
                assigned_set.append(best_drive)
                total_score += highest_score
                used_indices.add(best_idx)
            else:
                return {"valid": False, "score": 0.0}

        for req_shape in extra_shapes:
            candidates = [
                (idx, drive) for idx, drive in drive_buckets.get(req_shape, [])
                if idx not in used_indices
            ]
            picked = self._pick_best_drive(role_name, candidates, crit_mode)
            if picked:
                best_idx, best_drive, highest_score = picked
                assigned_extra.append(best_drive)
                total_score += highest_score
                used_indices.add(best_idx)
            else:
                return {"valid": False, "score": 0.0}

        return {"valid": True, "blueprint": blueprint, "assigned_set_drives": assigned_set,
                "assigned_extra_drives": assigned_extra, "score": round(total_score, 2)}

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
        seen = set()
        unique = []
        for blueprint in blueprints:
            key = (
                tuple(sorted(str(shape) for shape in blueprint.get("set_pieces", []))),
                tuple(sorted(str(shape) for shape in blueprint.get("extra_pieces", []))),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(blueprint)
        return unique

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
                ranking_matrix[slot_idx, drive_idx] = self._rank_score_for_drive(
                    slot["role"], drive, score, crit_priority_modes.get(slot["role"])
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

    def _find_best_group_fit(
        self,
        group: list[str],
        drives_pool: list[Drive],
        custom_sets: Dict[str, str],
        assigned_tapes: Dict[str, Tape],
        crit_priority_modes: Dict[str, dict],
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
            team_score = sum(item["score"] for item in temp_alloc.values())
            is_valid = True
            for slot_idx, drive_idx in zip(row_ind, col_ind):
                profit = profit_matrix[slot_idx, drive_idx]
                if profit < 0:
                    is_valid = False
                    break
                slot = slots[slot_idx]
                drive = drives_pool[drive_idx]
                role = slot["role"]
                temp_alloc[role]["blueprint"] = slot["bp"]
                if slot["type"] == "set":
                    temp_alloc[role]["assigned_set_drives"].append(drive)
                else:
                    temp_alloc[role]["assigned_extra_drives"].append(drive)
                temp_alloc[role]["score"] += profit
                team_score += profit
            if is_valid and team_score > best_score:
                best_score = team_score
                best_allocation = temp_alloc

        for role in group:
            best_allocation.setdefault(role, {"valid": False})
        return best_allocation

    def execute(self, candidate_pool: CandidatePool, priority_list: List[str], custom_sets: CustomSetMap,
                crit_priority_modes: StatPriorityConfigMap = None,
                priority_groups: list[list[str]] | None = None) -> AllocationResult:
        logger.info("启动分配模式: 角色优先")

        drives_pool = list(candidate_pool.get("drives", []))
        tapes_pool = candidate_pool.get("tapes", {})
        crit_priority_modes = crit_priority_modes or {}
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

            best_plan = {"valid": False, "score": -1.0}

            for bp in blueprints:
                plan = self._find_best_fit(role_name, bp, role_drives_pool, target_set, crit_priority_modes.get(role_name))
                if plan["valid"]:
                    total_score = plan["score"] + tape_score
                    if total_score > best_plan["score"]:
                        plan["score"] = total_score
                        plan["assigned_tape"] = role_tape
                        best_plan = plan

            if best_plan["valid"]:
                final_allocation[role_name] = best_plan
                used_uids = set(d.uid for d in best_plan["assigned_set_drives"]) | set(d.uid for d in best_plan["assigned_extra_drives"])
                drives_pool = [d for d in drives_pool if d.uid not in used_uids]
            else:
                final_allocation[role_name] = {"valid": False}

        return final_allocation

class MatrixBaseStrategy(BaseDispatchStrategy):
    """Shared helpers for matrix-based allocation strategies."""

    def _build_matrix_environment(self, priority_list):
        role_blueprints_list, valid_roles = [], []
        for role in priority_list:
            raw_bps = self.blueprints_db.get(role, [])
            bps = self._dedupe_blueprints_by_extra_pieces(raw_bps)
            if bps:
                if len(bps) < len(raw_bps):
                    logger.info(f"角色 [{role}] 图纸形状组合去重: {len(raw_bps)} -> {len(bps)}")
                role_blueprints_list.append(bps)
                valid_roles.append(role)
            else:
                logger.warning(f"角色 [{role}] 没有合法图纸，跳过分配。")
        return role_blueprints_list, valid_roles

class DrivePriorityStrategy(MatrixBaseStrategy):
    """Greedy best-drive-first allocation via profit matrix."""
    def execute(self, candidate_pool: CandidatePool, priority_list: List[str], custom_sets: CustomSetMap,
                crit_priority_modes: StatPriorityConfigMap = None) -> AllocationResult:
        logger.info("启动分配模式: 驱动优先")
        drives_pool = candidate_pool.get("drives", [])
        assigned_tapes = self._pre_allocate_tapes_optimal(priority_list, custom_sets, candidate_pool.get("tapes", {}))
        role_bps_list, valid_roles = self._build_matrix_environment(priority_list)
        if not valid_roles: return {}

        best_team_score, best_allocation = -1.0, {}
        combo_count = 0

        for bp_combo in self._iter_bp_combos(role_bps_list, valid_roles, drives_pool, custom_sets, crit_priority_modes):
            combo_count += 1
            if combo_count % 50 == 0:
                logger.info(f"  驱动优先: 已评估 {combo_count} 组图纸组合...")

            slots, profit_matrix, ranking_matrix = self._build_profit_matrix(
                bp_combo, valid_roles, drives_pool, custom_sets, crit_priority_modes
            )
            if slots is None: continue

            work_matrix = np.copy(ranking_matrix)
            is_valid = True
            temp_alloc = self._init_temp_alloc(valid_roles, assigned_tapes)
            team_score = sum(alloc["score"] for alloc in temp_alloc.values())
            pick_order = 1

            for _ in range(len(slots)):
                max_val = np.max(work_matrix)
                if max_val < 0:
                    is_valid = False
                    break

                r_idx, c_idx = np.unravel_index(np.argmax(work_matrix), work_matrix.shape)
                slot, drive = slots[r_idx], copy.deepcopy(drives_pool[c_idx])
                role = slot["role"]
                real_score = profit_matrix[r_idx, c_idx]

                drive.is_mvp = True
                drive.pick_order = pick_order
                pick_order += 1

                temp_alloc[role]["blueprint"] = slot["bp"]
                if slot["type"] == "set": temp_alloc[role]["assigned_set_drives"].append(drive)
                else: temp_alloc[role]["assigned_extra_drives"].append(drive)

                temp_alloc[role]["score"] += real_score
                team_score += real_score
                work_matrix[r_idx, :] = -10000.0
                work_matrix[:, c_idx] = -10000.0

            if is_valid and team_score > best_team_score:
                best_team_score, best_allocation = team_score, temp_alloc

        logger.info(f"  驱动优先: 评估完毕，共 {combo_count} 组。")
        return best_allocation

class GlobalOptimalStrategy(MatrixBaseStrategy):
    """Optimal allocation via Hungarian algorithm."""
    def execute(self, candidate_pool: CandidatePool, priority_list: List[str], custom_sets: CustomSetMap,
                crit_priority_modes: StatPriorityConfigMap = None) -> AllocationResult:
        logger.info("启动分配模式: 全局最优 (匈牙利算法)")
        drives_pool = candidate_pool.get("drives", [])
        assigned_tapes = self._pre_allocate_tapes_optimal(priority_list, custom_sets, candidate_pool.get("tapes", {}))
        role_bps_list, valid_roles = self._build_matrix_environment(priority_list)
        if not valid_roles: return {}

        best_team_score, best_allocation = -1.0, {}
        combo_count = 0

        for bp_combo in self._iter_bp_combos(role_bps_list, valid_roles, drives_pool, custom_sets, crit_priority_modes):
            combo_count += 1
            if combo_count % 50 == 0:
                logger.info(f"  全局最优: 已评估 {combo_count} 组图纸组合...")

            slots, profit_matrix, ranking_matrix = self._build_profit_matrix(
                bp_combo, valid_roles, drives_pool, custom_sets, crit_priority_modes
            )
            if slots is None: continue

            cost_matrix = -ranking_matrix
            row_ind, col_ind = linear_sum_assignment(cost_matrix)

            is_valid = True
            temp_alloc = self._init_temp_alloc(valid_roles, assigned_tapes)
            team_score = sum(alloc["score"] for alloc in temp_alloc.values())

            for r_idx, c_idx in zip(row_ind, col_ind):
                profit = profit_matrix[r_idx, c_idx]
                if profit < 0:
                    is_valid = False
                    break

                slot, drive = slots[r_idx], drives_pool[c_idx]
                role = slot["role"]

                temp_alloc[role]["blueprint"] = slot["bp"]
                if slot["type"] == "set": temp_alloc[role]["assigned_set_drives"].append(drive)
                else: temp_alloc[role]["assigned_extra_drives"].append(drive)

                temp_alloc[role]["score"] += profit
                team_score += profit

            if is_valid and team_score > best_team_score:
                best_team_score, best_allocation = team_score, temp_alloc

        logger.info(f"  全局最优: 评估完毕，共 {combo_count} 组。")
        return best_allocation
