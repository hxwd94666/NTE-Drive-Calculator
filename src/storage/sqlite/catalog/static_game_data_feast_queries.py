# 统一投影赏宴正式目标与各自难度画像，旧 schema 的单目标读取不升级为多目标假定。
from __future__ import annotations

from .static_feast_roster import feast_difficulty_targets


class FeastTargetQueriesMixin:
    def _has_explicit_feast_targets(self):
        # 同号历史 schema 来自两条发行线；正式目标能力由字段证据确定。
        return bool(self._one("SELECT name FROM pragma_table_info('feast_stage_difficulty') WHERE name='boss_static_table'"))

    def list_feast_stage_difficulties(self, stage_id: str):
        # 两条历史发行线的 41/42 结构不同；读取真实列，不凭相同版本号猜字段。
        explicit = self._has_explicit_feast_targets()
        identity = ("d.boss_monster_id,d.boss_static_table" if explicit else
                    "s.boss_monster_id,'monster_static_big_world' AS boss_static_table")
        rows = self._rows(
            "SELECT d.*," + identity + ",p.health_base,p.health_up,p.health_add,p.defense_base,"
            "p.defense_up,p.defense_add,p.topple_limit FROM feast_stage_difficulty d "
            "JOIN feast_stage s USING(stage_id) LEFT JOIN enemy_combat_profile p "
            "ON p.profile_set=d.profile_set AND p.pack_id=d.pack_id WHERE d.stage_id=? "
            "ORDER BY d.difficulty_id,boss_monster_id", (str(stage_id),),
        )
        for row in rows:
            profile = self.get_enemy_combat_profile(row["profile_set"], row["pack_id"])
            row["profile_available"] = profile is not None
            row["resistances"] = (profile or {}).get("resistances", {})
        return rows

    def get_feast_stage_data(self, stage_id: str):
        stage = self._one("SELECT * FROM feast_stage WHERE stage_id=?", (str(stage_id),))
        if stage is None:
            return None
        stage["difficulties"] = self.list_feast_stage_difficulties(stage_id)
        roster_available = self._one("SELECT name FROM sqlite_master WHERE type='table' AND name='clone_spawn_member'")
        explicit = bool(self._has_explicit_feast_targets())
        targets = {}
        for row in stage["difficulties"]:
            if roster_available:
                roster = feast_difficulty_targets(self, stage, row)
                if roster or row.get("unresolved_target_ids") or row.get("roster_complete"):
                    row["targets"] = roster
            if explicit and "targets" not in row:
                # 历史独立目录可只保留已规范化目标；不把空刷怪表解释成目标消失。
                row["targets"] = [{
                    "target_id": member["boss_monster_id"], "monster_class_path": member["boss_monster_id"],
                    "name_zh": member.get("boss_name_zh"), "monster_level": member["monster_level"],
                    "profile_set": member["profile_set"], "pack_id": member["pack_id"],
                    "profile": {key: member.get(key) for key in ("health_base", "health_up", "health_add",
                        "defense_base", "defense_up", "defense_add", "topple_limit", "resistances")},
                } for member in stage["difficulties"] if member["difficulty_id"] == row["difficulty_id"]
                   and member.get("profile_available")]
            key = (row["boss_static_table"], row["boss_monster_id"])
            target = targets.setdefault(key, {
                "target_id": row["boss_monster_id"], "boss_monster_id": row["boss_monster_id"],
                "boss_static_table": row["boss_static_table"], "name_zh": row.get("boss_name_zh"), "difficulties": [],
            })
            target["difficulties"].append(row)
        stage["targets"] = list(targets.values())
        stage["boss_monster_id"] = next(iter(targets))[1] if len(targets) == 1 else None
        mechanisms = self._one("SELECT name FROM sqlite_master WHERE type='view' AND name='feast_all_stage_option'")
        option_table = "feast_all_stage_option" if mechanisms else "feast_stage_option"
        detail_columns = ",d.description_zh,d.player_buffs_json,d.boss_buffs_json,d.option_node_text" if mechanisms else ""
        detail_join = "LEFT JOIN feast_mechanism_detail d USING(option_id)" if mechanisms else ""
        stage["options"] = self._rows(
            "SELECT s.category_ordinal,s.option_ordinal,s.category_name_zh,o.*,"
            f"ge.gameplay_effect_id AS mechanism_effect_id {detail_columns} FROM {option_table} s "
            f"JOIN feast_option o USING(option_id) {detail_join} LEFT JOIN gameplay_effect_catalog ge "
            "ON ge.class_path=o.buff_asset_path COLLATE BINARY WHERE s.stage_id=? "
            "ORDER BY s.category_ordinal,s.option_ordinal", (str(stage_id),),
        )
        categories = {}
        effects_available = self._one("SELECT name FROM sqlite_master WHERE type='table' AND name='feast_option_effect'")
        for row in stage["options"]:
            # 原始效果路径用于审计受体，不据此生成已观测 Buff 或常驻面板。
            row["effect_sources"] = self._rows(
                "SELECT recipient_kind,effect_ordinal,buff_asset_path,source_row_id "
                "FROM feast_option_effect WHERE option_id=? ORDER BY recipient_kind,effect_ordinal",
                (row["option_id"],),
            ) if effects_available else []
            category = categories.setdefault(row["category_ordinal"], {
                "category_ordinal": row["category_ordinal"], "name_zh": row["category_name_zh"], "options": [],
            })
            category["options"].append(row)
        stage["option_categories"] = list(categories.values())
        return stage

    def list_feast_stages(self):
        return [self.get_feast_stage_data(row["stage_id"]) for row in self._rows(
            "SELECT stage_id FROM feast_stage ORDER BY CAST(substr(stage_id,13) AS INTEGER),stage_id")]
