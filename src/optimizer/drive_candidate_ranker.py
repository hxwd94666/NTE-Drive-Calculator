# 驱动候选排序、隐藏分、自选词条优先级和卡带预分配的公共能力。
import re
import numpy as np
from scipy.optimize import linear_sum_assignment
from typing import List, Dict

from src.domain.stat_catalog import StatCatalog
from src.models.equipment import Drive, Tape
from src.optimizer.contracts import AllocationResult, CandidatePool, CustomSetMap, StatPriorityConfigMap
from src.utils.name_resolver import resolve_name

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
