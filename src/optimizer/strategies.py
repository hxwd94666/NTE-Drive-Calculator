# 实现角色优先、驱动优先和全局最优分配策略。
"""Allocation strategies for role-first, item-first, and global matching."""

import copy
import heapq
import itertools
import re
import numpy as np
from scipy.optimize import linear_sum_assignment
from typing import List, Dict, Any

from src.domain.stat_catalog import StatCatalog
from src.utils.logger import logger
from src.utils.name_resolver import resolve_name
from src.models.equipment import Drive, Tape
from src.optimizer.contracts import AllocationResult, CandidatePool, CustomSetMap, StatPriorityConfigMap
from src.solver.blueprint_utils import blueprint_piece_signature, dedupe_blueprints_by_piece_signature

class BaseDispatchStrategy:
    MAX_COMBO_LIMIT = 500

    def __init__(self, roles_db: Dict, sets_db: Dict, blueprints_db: Dict[str, List[Dict]]):
        self.roles_db = roles_db
        self.sets_db = sets_db
        self.blueprints_db = blueprints_db
        self.stat_catalog = StatCatalog.from_config_dir()
        self._role_stat_weight_cache = {}
        self._max_single_weight_cache = {}
        self._extra_shape_factor_cache = {}
        self._extra_shape_hidden_bonus_cache = {}

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
        target_raw = str(stat_key or "").strip()
        if not target_raw:
            return False
        target = self.stat_catalog.normalize_stat_name(target_raw, is_percent="%" in target_raw) or target_raw
        for name in (getattr(item, "sub_stats", {}) or {}).keys():
            raw_name = str(name or "").strip()
            normalized = self.stat_catalog.normalize_stat_name(raw_name, is_percent="%" in raw_name) or raw_name
            if normalized == target:
                return True
        return False

    def _covered_stat_count(self, item, stats: list[str]) -> int:
        return sum(1 for stat_key in stats if self._item_has_stat(item, stat_key))

    def _role_stat_weight(self, role: str, stat_name: str) -> float:
        cache_key = (role, str(stat_name or "").strip())
        if cache_key in self._role_stat_weight_cache:
            return self._role_stat_weight_cache[cache_key]
        weights = (self.roles_db.get(role, {}) or {}).get("weights", {}) or {}
        names = [str(stat_name or "").strip()]
        normalized = self.stat_catalog.normalize_stat_name(names[0], is_percent="%" in names[0])
        if normalized:
            names.append(normalized)
        mapped_name = self.stat_catalog.flexible_weight_name(names[0])
        if mapped_name:
            names.append(mapped_name)

        for name in dict.fromkeys(n for n in names if n):
            try:
                weight = float(weights.get(name, 0.0) or 0.0)
            except (TypeError, ValueError):
                weight = 0.0
            if weight > 0:
                self._role_stat_weight_cache[cache_key] = weight
                return weight

        flat_names = {"攻击力", "防御力", "生命值"}
        for name in dict.fromkeys(n for n in names if n):
            if name in flat_names:
                continue
            try:
                weight = float(weights.get(f"{name}%", 0.0) or 0.0)
            except (TypeError, ValueError):
                weight = 0.0
            if weight > 0:
                self._role_stat_weight_cache[cache_key] = weight
                return weight
        self._role_stat_weight_cache[cache_key] = 0.0
        return 0.0

    def _max_single_sub_stat_weight(self, role: str) -> float:
        if role in self._max_single_weight_cache:
            return self._max_single_weight_cache[role]
        weights = (self.roles_db.get(role, {}) or {}).get("weights", {}) or {}
        values = []
        for name, value in weights.items():
            if any(keyword in str(name) for keyword in self.stat_catalog.main_only_keywords):
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if numeric > 0:
                values.append(numeric)
        result = max(values) if values else 1.0
        self._max_single_weight_cache[role] = result
        return result

    def _drive_area(self, drive: Drive) -> int:
        area = getattr(drive, "area", None)
        if area is None:
            numbers = re.findall(r"\d+", str(getattr(drive, "shape_id", "") or ""))
            area = int(numbers[0]) if numbers else 0
        return int(area or 0)

    def _extra_shape_bonus_factor(self, role: str) -> float:
        if role in self._extra_shape_factor_cache:
            return self._extra_shape_factor_cache[role]
        role_data = self.roles_db.get(role, {}) or {}
        extra_buffs = role_data.get("extra_shape_buffs", {}) or {}
        if not isinstance(extra_buffs, dict) or not extra_buffs:
            self._extra_shape_factor_cache[role] = 0.0
            return 0.0

        max_single_weight = self._max_single_sub_stat_weight(role)
        if max_single_weight <= 0:
            self._extra_shape_factor_cache[role] = 0.0
            return 0.0

        factor = 0.0
        for stat, raw_value in extra_buffs.items():
            normalized = self.stat_catalog.normalize_stat_name(str(stat or ""), is_percent="%" in str(stat or ""))
            stat_key = normalized or str(stat or "").strip()
            try:
                buff_value = float(raw_value)
                base_value = float(self.stat_catalog.gold_base_values.get(stat_key, 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if buff_value <= 0 or base_value <= 0:
                continue
            stat_weight = self._role_stat_weight(role, stat_key)
            if stat_weight <= 0:
                continue
            equivalent_grids = buff_value / base_value
            factor += (10.0 / max_single_weight) * stat_weight * equivalent_grids
        self._extra_shape_factor_cache[role] = factor
        return factor

    def _extra_shape_hidden_bonus(self, role: str, drive: Drive) -> float:
        cache_key = (role, getattr(drive, "uid", id(drive)))
        if cache_key in self._extra_shape_hidden_bonus_cache:
            return self._extra_shape_hidden_bonus_cache[cache_key]
        target_area = self._extra_shape_area_for_role(role)
        drive_area = self._drive_area(drive)
        if not target_area or drive_area <= 0 or drive_area != target_area:
            self._extra_shape_hidden_bonus_cache[cache_key] = 0.0
            return 0.0

        bonus = self._extra_shape_bonus_factor(role) / drive_area
        self._extra_shape_hidden_bonus_cache[cache_key] = bonus
        return bonus

    def _drive_ranking_score(
        self,
        role: str,
        drive: Drive,
        base_score: float,
        *,
        include_extra_shape_bonus: bool = True,
    ) -> float:
        if base_score < 0:
            return base_score
        score = float(base_score or 0.0)
        if include_extra_shape_bonus:
            score += self._extra_shape_hidden_bonus(role, drive)
        return score

    def _stat_priority_enabled(self, config) -> bool:
        cfg = self._stat_priority_config(config)
        return bool(cfg.get("stats"))

    def _stat_priority_applies_to_item(self, role: str, item, config) -> bool:
        cfg = self._stat_priority_config(config)
        if not cfg.get("stats"):
            return False
        return bool(cfg.get("ignore_grade_limit")) or self._is_a_grade_item(role, item)

    def _stat_priority_depth(self, role: str, item, config) -> int:
        cfg = self._stat_priority_config(config)
        stats = cfg.get("stats", [])
        if not stats or not self._stat_priority_applies_to_item(role, item, cfg):
            return 0
        if cfg.get("equal_priority"):
            return self._covered_stat_count(item, stats)
        depth = 0
        for stat_key in stats:
            if not self._item_has_stat(item, stat_key):
                break
            depth += 1
        return depth

    def _stat_priority_key_for_items(self, role: str, items, config) -> tuple:
        cfg = self._stat_priority_config(config)
        stats = cfg.get("stats", [])
        if not stats:
            return ()
        counts = [0] * (len(stats) + 1)
        for item in items or []:
            depth = self._stat_priority_depth(role, item, cfg)
            if depth > 0:
                counts[min(depth, len(stats))] += 1
        return tuple(counts[depth] for depth in range(len(stats), 0, -1))

    def _stat_priority_total_hits(self, role: str, items, config) -> int:
        return sum(self._stat_priority_depth(role, item, config) for item in items or [])

    def _drive_pick_key(
        self,
        role: str,
        drive,
        base_score: float,
        config,
        include_extra_shape_bonus: bool = True,
    ) -> tuple:
        rank_score = self._drive_ranking_score(
            role,
            drive,
            base_score,
            include_extra_shape_bonus=include_extra_shape_bonus,
        )
        if self._stat_priority_enabled(config):
            return (self._stat_priority_depth(role, drive, config), rank_score)
        return (rank_score,)

    def _matches_stat_priority_pool(self, item, config) -> bool:
        cfg = self._stat_priority_config(config)
        stats = cfg.get("stats", [])
        if not stats or not cfg.get("ignore_grade_limit"):
            return True
        return self._covered_stat_count(item, stats) > 0

    def _stat_priority_hit_count(self, role: str, item, config) -> int:
        cfg = self._stat_priority_config(config)
        stats = cfg.get("stats", [])
        if not stats or not self._stat_priority_applies_to_item(role, item, cfg):
            return 0
        return self._covered_stat_count(item, stats)

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

    def _rank_score_for_drive(
        self,
        role: str,
        drive: Drive,
        base_score: float,
        config,
        *,
        include_extra_shape_bonus: bool = True,
    ) -> float:
        rank_score = self._drive_ranking_score(
            role,
            drive,
            base_score,
            include_extra_shape_bonus=include_extra_shape_bonus,
        )
        return self._rank_score_for_item(role, drive, rank_score, config)

    def _crit_rate_cap(self, role: str, crit_rate_caps: Dict[str, float] | None):
        if not crit_rate_caps or role not in crit_rate_caps:
            return None
        try:
            return float(crit_rate_caps[role])
        except (TypeError, ValueError):
            return None

    def _is_crit_rate_key(self, key: str) -> bool:
        normalized = str(key or "").replace("%", "")
        return "暴击率" in normalized or "鏆村嚮鐜" in normalized

    def _crit_rate_from_stats(self, stats) -> float:
        if not isinstance(stats, dict):
            return 0.0
        total = 0.0
        for key, value in stats.items():
            if not self._is_crit_rate_key(key):
                continue
            try:
                total += float(value)
            except (TypeError, ValueError):
                continue
        return total

    def _item_crit_rate(self, item) -> float:
        total = self._crit_rate_from_stats(getattr(item, "sub_stats", {}) or {})
        main_stats = getattr(item, "main_stats", {}) or {}
        total += self._crit_rate_from_stats(main_stats)
        if isinstance(main_stats, str) and self._is_crit_rate_key(main_stats):
            total += 30.0
        return total

    def _items_crit_rate(self, items) -> float:
        return sum(self._item_crit_rate(item) for item in items or [] if item)

    def _extra_shape_area_for_role(self, role: str):
        label = str(self.roles_db.get(role, {}).get("extra_shape_label", "") or "")
        match = re.search(r"(\d+)", label)
        if not match:
            return None
        return int(match.group(1))

    def _extra_shape_crit_rate(self, role: str, items) -> float:
        role_data = self.roles_db.get(role, {}) or {}
        extra_buffs = role_data.get("extra_shape_buffs", {}) or {}
        if not isinstance(extra_buffs, dict) or not extra_buffs:
            return 0.0
        target_area = self._extra_shape_area_for_role(role)
        if not target_area:
            return 0.0
        crit_bonus = 0.0
        for stat, value in extra_buffs.items():
            if not self._is_crit_rate_key(stat):
                continue
            try:
                crit_bonus += float(value)
            except (TypeError, ValueError):
                continue
        if crit_bonus <= 0:
            return 0.0
        matched_count = 0
        for item in items or []:
            if not isinstance(item, Drive):
                continue
            if self._drive_area(item) == target_area:
                matched_count += 1
        return crit_bonus * matched_count

    def _within_crit_rate_cap(self, role: str, items, crit_rate_caps: Dict[str, float] | None) -> bool:
        cap = self._crit_rate_cap(role, crit_rate_caps)
        if cap is None:
            return True
        total = self._items_crit_rate(items) + self._extra_shape_crit_rate(role, items)
        return total <= cap + 1e-9

    def _set_pieces_for_blueprint(self, blueprint: Dict, target_set: str) -> list[str]:
        if "set_pieces" in blueprint:
            return list(blueprint.get("set_pieces") or [])
        return list(self.sets_db[target_set]["shapes"])

    def _slot_uses_extra_shape_bonus(self, slot_type: str, blueprint: Dict | None, include_extra_shape_bonus: bool = True) -> bool:
        if not include_extra_shape_bonus:
            return False
        if slot_type != "set":
            return False
        return (blueprint or {}).get("set_effect_mode") == "two_piece"

    def _pick_best_drive(
        self,
        role: str,
        candidates: list[tuple[int, Drive]],
        config=None,
        include_extra_shape_bonus: bool = True,
    ) -> tuple[int, Drive, float, float] | None:
        if not candidates:
            return None
        ranked = [
            (
                self._drive_pick_key(
                    role,
                    drive,
                    drive.role_scores.get(role, 0.0),
                    config,
                    include_extra_shape_bonus=include_extra_shape_bonus,
                ),
                idx,
                drive,
                self._drive_ranking_score(
                    role,
                    drive,
                    drive.role_scores.get(role, 0.0),
                    include_extra_shape_bonus=include_extra_shape_bonus,
                ),
            )
            for idx, drive in candidates
        ]
        _rank_key, idx, drive, rank_score = max(ranked, key=lambda item: item[0])
        return idx, drive, drive.role_scores.get(role, 0.0), rank_score

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
        return blueprint_piece_signature(blueprint)

    def _dedupe_blueprints_by_extra_pieces(self, blueprints):
        return dedupe_blueprints_by_piece_signature(blueprints)

    def _shape_score_buckets(self, role, drives_pool, crit_config=None, include_extra_shape_bonus: bool = True):
        buckets = {}
        for drive in drives_pool or []:
            base_score = drive.role_scores.get(role, 0.0)
            rank_score = self._rank_score_for_drive(
                role,
                drive,
                base_score,
                crit_config,
                include_extra_shape_bonus=include_extra_shape_bonus,
            )
            buckets.setdefault(drive.shape_id, []).append(rank_score)
        for scores in buckets.values():
            scores.sort(reverse=True)
        return buckets

    def _blueprint_theoretical_score(
        self,
        role,
        blueprint,
        drives_pool,
        custom_sets,
        crit_config=None,
        include_extra_shape_bonus: bool = True,
    ):
        target_set = self._target_set(role, custom_sets)
        set_uses_bonus = self._slot_uses_extra_shape_bonus("set", blueprint, include_extra_shape_bonus)
        set_buckets = self._shape_score_buckets(
            role,
            drives_pool,
            crit_config,
            include_extra_shape_bonus=set_uses_bonus,
        )
        extra_buckets = self._shape_score_buckets(
            role,
            drives_pool,
            crit_config,
            include_extra_shape_bonus=False,
        )
        used_counts = {}
        total = 0.0
        required_slots = [
            ("set", shape) for shape in self._set_pieces_for_blueprint(blueprint, target_set)
        ] + [
            ("extra", shape) for shape in list(blueprint.get("extra_pieces", []))
        ]
        for slot_type, shape in required_slots:
            bucket_key = (slot_type, shape)
            buckets = set_buckets if slot_type == "set" else extra_buckets
            used = used_counts.get(bucket_key, 0)
            scores = buckets.get(shape, [])
            if used >= len(scores):
                return -10000.0
            total += scores[used]
            used_counts[bucket_key] = used + 1
        return total

    def _rank_role_blueprints(
        self,
        role_bps_list,
        valid_roles,
        drives_pool,
        custom_sets,
        crit_priority_modes=None,
        include_extra_shape_bonus: bool = True,
    ):
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
                        include_extra_shape_bonus=include_extra_shape_bonus,
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

    def _iter_bp_combos(
        self,
        role_bps_list,
        valid_roles=None,
        drives_pool=None,
        custom_sets=None,
        crit_priority_modes=None,
        include_extra_shape_bonus: bool = True,
    ):
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
                include_extra_shape_bonus=include_extra_shape_bonus,
            )
            yield from self._iter_ranked_bp_combos(ranked_role_bps)

    def _build_profit_matrix(
        self,
        bp_combo,
        valid_roles,
        drives_pool,
        custom_sets,
        crit_priority_modes=None,
        include_extra_shape_bonus: bool = True,
    ):
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
                    slot_uses_bonus = self._slot_uses_extra_shape_bonus(
                        slot["type"],
                        slot.get("bp"),
                        include_extra_shape_bonus,
                    )
                    ranking_matrix[i, j] = self._rank_score_for_drive(
                        slot["role"],
                        drive,
                        score,
                        crit_priority_modes.get(slot["role"]),
                        include_extra_shape_bonus=slot_uses_bonus,
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
                priority_groups: list[list[str]] | None = None,
                crit_rate_caps: Dict[str, float] | None = None) -> AllocationResult:
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

        best_team_score, best_team_rank_score, best_allocation = -1.0, -1.0, {}
        best_priority_hits = -1
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
            team_rank_score = team_score
            priority_hits = 0
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
                rank_score = ranking_matrix[r_idx, c_idx]

                drive.is_mvp = True
                drive.pick_order = pick_order
                pick_order += 1

                temp_alloc[role]["blueprint"] = slot["bp"]
                if slot["type"] == "set": temp_alloc[role]["assigned_set_drives"].append(drive)
                else: temp_alloc[role]["assigned_extra_drives"].append(drive)

                temp_alloc[role]["score"] += real_score
                team_score += real_score
                team_rank_score += rank_score
                priority_hits += self._stat_priority_hit_count(
                    role, drive, crit_priority_modes.get(role)
                )
                work_matrix[r_idx, :] = -10000.0
                work_matrix[:, c_idx] = -10000.0

            if is_valid and (priority_hits, team_rank_score, team_score) > (
                best_priority_hits,
                best_team_rank_score,
                best_team_score,
            ):
                best_priority_hits = priority_hits
                best_team_rank_score = team_rank_score
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
        best_priority_hits = -1
        combo_count = 0

        for bp_combo in self._iter_bp_combos(
            role_bps_list,
            valid_roles,
            drives_pool,
            custom_sets,
            crit_priority_modes,
            include_extra_shape_bonus=False,
        ):
            combo_count += 1
            if combo_count % 50 == 0:
                logger.info(f"  全局最优: 已评估 {combo_count} 组图纸组合...")

            slots, profit_matrix, ranking_matrix = self._build_profit_matrix(
                bp_combo,
                valid_roles,
                drives_pool,
                custom_sets,
                crit_priority_modes,
                include_extra_shape_bonus=False,
            )
            if slots is None: continue

            cost_matrix = -ranking_matrix
            row_ind, col_ind = linear_sum_assignment(cost_matrix)

            is_valid = True
            temp_alloc = self._init_temp_alloc(valid_roles, assigned_tapes)
            team_score = sum(alloc["score"] for alloc in temp_alloc.values())
            priority_hits = 0

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
                priority_hits += self._stat_priority_hit_count(
                    role, drive, crit_priority_modes.get(role)
                )

            if is_valid and (priority_hits, team_score) > (best_priority_hits, best_team_score):
                best_priority_hits = priority_hits
                best_team_score, best_allocation = team_score, temp_alloc

        logger.info(f"  全局最优: 评估完毕，共 {combo_count} 组。")
        return best_allocation
