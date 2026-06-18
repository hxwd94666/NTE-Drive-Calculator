# 计算装备词条和套装评分。
"""Equipment scoring rules driven by role and stat configuration."""

import json
import os
from typing import List, Dict, Any

from src.domain.stat_catalog import StatCatalog
from src.utils.logger import logger
from src.models.equipment import BaseEquipment, Drive, Tape


class ScoringEngine:

    def __init__(self, config_dir: str = "config"):
        self.config_dir = config_dir
        self.roles_db = {}
        self.stat_catalog = StatCatalog()
        self.gold_base_values = {}
        self.main_only_keywords = []
        self.stat_alias_mapping = {}
        self.quality_map = {"Gold": 1.0, "Purple": 0.8, "Blue": 0.6}
        self._load_configs()

    def _load_configs(self):
        roles_path = os.path.join(self.config_dir, 'roles.json')
        if os.path.exists(roles_path):
            with open(roles_path, 'r', encoding='utf-8') as f:
                self.roles_db = json.load(f)
        else:
            logger.warning(f"找不到角色配置文件: {roles_path}")

        stats_path = os.path.join(self.config_dir, 'stats.json')
        if os.path.exists(stats_path):
            self.stat_catalog = StatCatalog.from_config_dir(self.config_dir)
            self.gold_base_values = self.stat_catalog.gold_base_values
            self.main_only_keywords = self.stat_catalog.main_only_keywords
            self.stat_alias_mapping = self.stat_catalog.stat_alias_mapping
        else:
            logger.error(f"找不到数值规则文件: {stats_path}")

    def _get_max_theoretical_weight(self, weights: Dict[str, float]) -> float:
        if not weights: return 1.0
        valid_sub_weights = [w for name, w in weights.items() if not any(kw in name for kw in self.main_only_keywords)]
        sorted_weights = sorted(valid_sub_weights, reverse=True)
        max_sub_weight = sum(sorted_weights[:4])
        return max_sub_weight if max_sub_weight > 0 else 1.0

    def _get_flexible_weight(self, stat_name: str, weights: Dict[str, float]) -> float:
        names = [str(stat_name or "").strip()]
        normalized = self.stat_catalog.normalize_stat_name(names[0], is_percent="%" in names[0])
        if normalized:
            names.append(normalized)
        mapped_name = self.stat_catalog.flexible_weight_name(names[0])
        if mapped_name:
            names.append(mapped_name)

        for name in dict.fromkeys(n for n in names if n):
            w = weights.get(name, 0.0)
            if w > 0:
                return w

        flat_names = {"攻击力", "防御力", "生命值"}
        for name in dict.fromkeys(n for n in names if n):
            if name not in flat_names:
                w = weights.get(f"{name}%", 0.0)
                if w > 0:
                    return w
        return 0.0

    def calculate_drive_score(self, drive: Drive, weights: Dict[str, float], max_weight: float) -> float:
        if max_weight <= 0: return 0.0
        actual_weight = sum(self._get_flexible_weight(stat_name, weights) for stat_name in drive.sub_stats.keys())
        if actual_weight <= 0: return 0.0

        quality_coef = self.quality_map.get(drive.quality, 1.0)
        score = (10.0 / max_weight) * actual_weight * drive.area * quality_coef
        return round(score, 2)

    def calculate_cartridge_score(self, tape: Tape, weights: dict, max_weight: float) -> float:
        if max_weight <= 0: return 0.0

        quality_coef = self.quality_map.get(tape.quality, 1.0)

        main_stat_name = tape.main_stats
        main_weight = self._get_flexible_weight(main_stat_name, weights)
        main_score = main_weight * 50.0 * quality_coef

        sub_weight = sum(self._get_flexible_weight(stat_name, weights) for stat_name in tape.sub_stats.keys())
        sub_score = (10.0 / max_weight) * sub_weight * 10.0 * quality_coef

        return round(main_score + sub_score, 2)

    def evaluate_global_inventory(self, inventory: List[BaseEquipment], top_k_per_shape_per_role: int = 15) -> Dict[str, Any]:
        if not self.roles_db: return {"drives": [], "tapes": {}}
        logger.info(f"  评分引擎: 开始评估 {len(inventory)} 件装备 × {len(self.roles_db)} 角色...")

        valid_drives: List[Drive] = []
        valid_tapes: List[Tape] = []

        for item in inventory:
            item.role_scores = {}
            item.max_score = 0.0

            for role_name, role_data in self.roles_db.items():
                weights = role_data.get("weights", {})
                max_weight = self._get_max_theoretical_weight(weights)

                if isinstance(item, Drive):
                    score = self.calculate_drive_score(item, weights, max_weight)
                else:
                    score = self.calculate_cartridge_score(item, weights, max_weight)

                item.role_scores[role_name] = score
                if score > item.max_score:
                    item.max_score = score

            if item.max_score > 0:
                if isinstance(item, Drive):
                    valid_drives.append(item)
                else:
                    valid_tapes.append(item)

        global_drive_uids = set()
        for role_name in self.roles_db.keys():
            buckets: Dict[str, List[Drive]] = {}
            for d in valid_drives:
                if d.role_scores[role_name] > 0:
                    buckets.setdefault(d.shape_id, []).append(d)

            for shape, drives_in_bucket in buckets.items():
                drives_in_bucket.sort(key=lambda x: x.role_scores[role_name], reverse=True)
                for d in drives_in_bucket[:top_k_per_shape_per_role]:
                    global_drive_uids.add(d.uid)

        optimal_drives = [d for d in valid_drives if d.uid in global_drive_uids]
        optimal_drives.sort(key=lambda x: x.max_score, reverse=True)

        optimal_tapes = {role: [] for role in self.roles_db.keys()}
        for role_name in self.roles_db.keys():
            role_tapes = [t for t in valid_tapes if t.role_scores[role_name] > 0]
            set_buckets = {}
            for t in role_tapes:
                set_buckets.setdefault(t.set_name, []).append(t)

            final_role_tapes = []
            for s_name, bucket in set_buckets.items():
                bucket.sort(key=lambda x: x.role_scores[role_name], reverse=True)
                final_role_tapes.extend(bucket[:3])

            final_role_tapes.sort(key=lambda x: x.role_scores[role_name], reverse=True)
            optimal_tapes[role_name] = final_role_tapes

        return {"drives": optimal_drives, "tapes": optimal_tapes}

    def get_grade_tag(self, score: float, area: int) -> str:
        max_possible_score = area * 10.0
        if max_possible_score == 0: return "D"
        ratio = score / max_possible_score
        if ratio >= 0.8: return "ACE"
        elif ratio >= 0.7: return "SSS"
        elif ratio >= 0.6: return "SS"
        elif ratio >= 0.5: return "S"
        elif ratio >= 0.4: return "A"
        elif ratio >= 0.3: return "B"
        elif ratio >= 0.2: return "C"
        else: return "D"
