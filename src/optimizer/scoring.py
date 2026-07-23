# 计算装备词条和套装评分。
"""Equipment scoring rules driven by role and stat configuration."""

from pathlib import Path
from typing import List, Dict, Any, Mapping

from src.domain.crit_threshold import meets_preference_grade_limit
from src.domain.grade_limits import meets_min_grade
from src.domain.stat_catalog import StatCatalog
from src.utils.logger import logger
from src.models.equipment import BaseEquipment, Drive, Tape
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


# 旧评分器仍以这些显示名匹配 OCR 装备；角色和权重本身已迁到 SQLite。
_LEGACY_PROPERTY_NAMES = {
    "AtkAdd": "攻击力", "AtkUp": "攻击力%", "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%", "DamageUpChaosBase": "暗属性异能伤害增强%",
    "DamageUpCosmosBase": "光属性异能伤害增强%", "DamageUpGeneralAdd": "伤害增加%",
    "DamageUpGeneralBase": "伤害增加%", "DamageUpIncantationBase": "咒属性异能伤害增强%",
    "DamageUpLakshanaBase": "相属性异能伤害增强%", "DamageUpNatureBase": "灵属性异能伤害增强%",
    "DamageUpPsycheBase": "魂属性异能伤害增强%", "DamageUpPsychicallyBase": "心灵伤害增强%",
    "DefAdd": "防御力", "DefUp": "防御力%", "HealUp": "治疗加成",
    "HPMaxAdd": "生命值", "HPMaxUp": "生命值%", "MagBase": "环合强度",
    "UnbalIntensityBase": "倾陷强度",
}


class ScoringEngine:

    def __init__(
        self,
        config_dir: str = "config",
        *,
        user_database_path: str | Path | None = None,
        roles_db: Mapping[str, Mapping[str, Any]] | None = None,
    ):
        self.config_dir = str(config_dir)
        self.user_database_path = (
            Path(user_database_path) if user_database_path is not None else None
        )
        self.roles_db = dict(roles_db or {})
        self.stat_catalog = StatCatalog()
        self.gold_base_values = {}
        self.main_only_keywords = []
        self.stat_alias_mapping = {}
        self.quality_map = {"Gold": 1.0, "Purple": 0.8, "Blue": 0.6}
        self._load_stats()
        if roles_db is None:
            self._load_roles_from_sqlite()

    def _load_stats(self):
        stats_path = Path(self.config_dir) / "stats.json"
        if stats_path.is_file():
            self.stat_catalog = StatCatalog.from_config_dir(self.config_dir)
            self.gold_base_values = self.stat_catalog.gold_base_values
            self.main_only_keywords = self.stat_catalog.main_only_keywords
            self.stat_alias_mapping = self.stat_catalog.stat_alias_mapping
        else:
            logger.error(f"找不到数值规则文件: {stats_path}")

    @staticmethod
    def _scoring_property_name(attribute: Mapping[str, Any]) -> str:
        property_id = str(attribute.get("attribute_id") or "")
        mapped = _LEGACY_PROPERTY_NAMES.get(property_id)
        if mapped:
            return mapped
        label = str(
            attribute.get("filter_name_zh")
            or attribute.get("display_name_zh")
            or property_id
        ).strip()
        if bool(attribute.get("show_percent")) and label and not label.endswith("%"):
            return f"{label}%"
        return label

    def _load_roles_from_sqlite(self) -> None:
        """Load scoring weights from account/static SQLite, never roles.json."""

        user_dao = None
        try:
            if self.user_database_path is not None and self.user_database_path.is_file():
                user_dao = UserDataDao(self.user_database_path)
                preferred_ids = user_dao.list_observed_character_ids()
            else:
                preferred_ids = ()
            with StaticGameDataDao() as static_dao:
                labels = {
                    str(attribute["attribute_id"]): self._scoring_property_name(attribute)
                    for attribute in static_dao.list_equipment_attributes()
                }
                for character in static_dao.list_role_template_characters(preferred_ids):
                    character_id = int(character["character_id"])
                    record = (
                        user_dao.get_character_weight_preferences(character_id)
                        if user_dao is not None
                        else None
                    )
                    if record is None:
                        record = static_dao.get_character_recommended_weights(character_id)
                    if record is None:
                        continue
                    weights = {
                        labels[property_id]: float(weight)
                        for property_id, weight in (record.get("property_weights") or {}).items()
                        if labels.get(str(property_id)) and float(weight) > 0
                    }
                    main_weights = {
                        labels[property_id]: float(weight)
                        for property_id, weight in (record.get("main_property_weights") or {}).items()
                        if labels.get(str(property_id)) and float(weight) > 0
                    }
                    if weights or main_weights:
                        role_name = str(character.get("name_zh") or character_id)
                        self.roles_db[role_name] = {
                            "character_id": character_id,
                            "weights": weights,
                            "main_weights": main_weights,
                        }
        finally:
            if user_dao is not None:
                user_dao.close()

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
        for target_name in dict.fromkeys(n for n in names if n):
            for raw_name, weight in weights.items():
                if weight > 0 and self.stat_catalog.flexible_weight_name(raw_name) == target_name:
                    return weight

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

    def calculate_cartridge_score(self, tape: Tape, weights: dict, max_weight: float, main_weights: dict | None = None) -> float:
        if max_weight <= 0: return 0.0

        quality_coef = self.quality_map.get(tape.quality, 1.0)

        main_stat_name = tape.main_stats
        main_weight_source = main_weights if isinstance(main_weights, dict) else weights
        main_weight = self._get_flexible_weight(main_stat_name, main_weight_source)
        main_score = main_weight * 50.0 * quality_coef

        sub_weight = sum(self._get_flexible_weight(stat_name, weights) for stat_name in tape.sub_stats.keys())
        sub_score = (10.0 / max_weight) * sub_weight * 10.0 * quality_coef

        return round(main_score + sub_score, 2)

    def _is_a_grade_item(self, role: str, item: BaseEquipment) -> bool:
        score = getattr(item, "role_scores", {}).get(role, 0.0)
        area = getattr(item, "area", 1) or 1
        return meets_min_grade(score, area, "A")


    def _item_has_stat(self, item: BaseEquipment, stat_key: str) -> bool:
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

    def _priority_rank_for_item(self, role: str, item: BaseEquipment, config: dict | None) -> tuple[int, int]:
        if not isinstance(config, dict):
            return (0, 0)
        score = getattr(item, "role_scores", {}).get(role, 0.0)
        area = getattr(item, "area", 1) or 1
        if not meets_preference_grade_limit(score, area, config):
            return (0, 0)
        stats = [str(stat) for stat in config.get("stats", []) if stat]
        if not stats:
            return (0, 0)
        if config.get("equal_priority"):
            covered = sum(1 for stat in stats if self._item_has_stat(item, stat))
            return (covered, 0)
        for tier, stat in enumerate(stats):
            if self._item_has_stat(item, stat):
                return (len(stats) - tier, 0)
        return (0, 0)

    def _has_stat_priority_for_any_role(self, item: BaseEquipment, configs: Dict[str, dict]) -> bool:
        return any(self._priority_rank_for_item(role_name, item, config) > (0, 0) for role_name, config in configs.items())

    def _allowed_tape_main_names(self, allowed_mains: List[str] | None) -> set[str]:
        allowed = set()
        for value in allowed_mains or []:
            raw = str(value or "").strip()
            if not raw:
                continue
            allowed.add(raw)
            normalized = self.stat_catalog.normalize_tape_main_stat(raw)
            if normalized:
                allowed.add(normalized)
        return allowed

    def _tape_main_allowed(self, tape: Tape, allowed: set[str]) -> bool:
        if not allowed:
            return True
        raw = str(getattr(tape, "main_stats", "") or "").strip()
        normalized = self.stat_catalog.normalize_tape_main_stat(raw)
        return raw in allowed or normalized in allowed

    def evaluate_global_inventory(
        self,
        inventory: List[BaseEquipment],
        top_k_per_shape_per_role: int = 15,
        tape_top_k_per_set_per_role: int = 3,
        tape_main_filters: Dict[str, List[str]] | None = None,
        crit_priority_modes: Dict[str, dict] | None = None,
    ) -> Dict[str, Any]:
        if not self.roles_db: return {"drives": [], "tapes": {}}
        tape_main_filters = tape_main_filters or {}
        crit_priority_modes = crit_priority_modes or {}
        has_unlimited_stat_priority = any(
            isinstance(config, dict)
            and bool(config.get("ignore_grade_limit"))
            and bool(config.get("stats"))
            for config in crit_priority_modes.values()
        )
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
                    main_weights = role_data["main_weights"] if "main_weights" in role_data else None
                    score = self.calculate_cartridge_score(item, weights, max_weight, main_weights)

                item.role_scores[role_name] = score
                if score > item.max_score:
                    item.max_score = score

            if isinstance(item, Drive):
                if (
                    has_unlimited_stat_priority
                    or item.max_score > 0
                    or self._has_stat_priority_for_any_role(item, crit_priority_modes)
                ):
                    valid_drives.append(item)
            elif item.max_score > 0 or any(
                self._tape_main_allowed(item, self._allowed_tape_main_names(values))
                for values in tape_main_filters.values()
            ):
                valid_tapes.append(item)

        global_drive_uids = set()
        for role_name in self.roles_db.keys():
            role_priority_config = crit_priority_modes.get(role_name)
            role_unlimited_stat_priority = (
                isinstance(role_priority_config, dict)
                and bool(role_priority_config.get("ignore_grade_limit"))
                and bool(role_priority_config.get("stats"))
            )
            buckets: Dict[str, List[Drive]] = {}
            for d in valid_drives:
                priority_rank = self._priority_rank_for_item(role_name, d, role_priority_config)
                if role_unlimited_stat_priority or d.role_scores[role_name] > 0 or priority_rank > (0, 0):
                    buckets.setdefault(d.shape_id, []).append(d)

            for shape, drives_in_bucket in buckets.items():
                if role_unlimited_stat_priority:
                    for d in drives_in_bucket:
                        global_drive_uids.add(d.uid)
                    continue
                drives_in_bucket.sort(
                    key=lambda x: (
                        self._priority_rank_for_item(role_name, x, role_priority_config),
                        x.role_scores[role_name],
                    ),
                    reverse=True,
                )
                for d in drives_in_bucket[:top_k_per_shape_per_role]:
                    global_drive_uids.add(d.uid)

        optimal_drives = [d for d in valid_drives if d.uid in global_drive_uids]
        optimal_drives.sort(key=lambda x: x.max_score, reverse=True)

        optimal_tapes = {role: [] for role in self.roles_db.keys()}
        for role_name in self.roles_db.keys():
            allowed_mains = self._allowed_tape_main_names(tape_main_filters.get(role_name))
            role_tapes = [
                t
                for t in valid_tapes
                if t.role_scores[role_name] > 0 or (allowed_mains and self._tape_main_allowed(t, allowed_mains))
            ]
            role_tapes = [t for t in role_tapes if self._tape_main_allowed(t, allowed_mains)]
            set_buckets = {}
            for t in role_tapes:
                set_buckets.setdefault(t.set_name, []).append(t)

            final_role_tapes = []
            for s_name, bucket in set_buckets.items():
                bucket.sort(
                    key=lambda x: (
                        self._priority_rank_for_item(role_name, x, crit_priority_modes.get(role_name)),
                        x.role_scores[role_name],
                    ),
                    reverse=True,
                )
                final_role_tapes.extend(bucket[:tape_top_k_per_set_per_role])

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
