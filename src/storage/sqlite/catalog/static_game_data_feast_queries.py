# 统一投影赏宴正式目标与各自难度画像，旧 schema 的单目标读取不升级为多目标假定。
from __future__ import annotations


class FeastTargetQueriesMixin:
    def list_feast_stage_difficulties(self, stage_id: str):
        identity = ("d.boss_monster_id,d.boss_static_table" if self.schema_version >= 41 else
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
        targets = {}
        for row in stage["difficulties"]:
            key = (row["boss_static_table"], row["boss_monster_id"])
            target = targets.setdefault(key, {
                "target_id": row["boss_monster_id"], "boss_monster_id": row["boss_monster_id"],
                "boss_static_table": row["boss_static_table"], "name_zh": row.get("boss_name_zh"), "difficulties": [],
            })
            target["difficulties"].append(row)
        stage["targets"] = list(targets.values())
        stage["boss_monster_id"] = next(iter(targets))[1] if len(targets) == 1 else None
        stage["options"] = self._rows(
            "SELECT s.category_ordinal,s.option_ordinal,s.category_name_zh,o.*,"
            "ge.gameplay_effect_id AS mechanism_effect_id FROM feast_stage_option s "
            "JOIN feast_option o USING(option_id) LEFT JOIN gameplay_effect_catalog ge "
            "ON ge.class_path=o.buff_asset_path COLLATE BINARY WHERE s.stage_id=? "
            "ORDER BY s.category_ordinal,s.option_ordinal", (str(stage_id),),
        )
        categories = {}
        for row in stage["options"]:
            # 原始效果路径用于审计受体，不据此生成已观测 Buff 或常驻面板。
            row["effect_sources"] = self._rows(
                "SELECT recipient_kind,effect_ordinal,buff_asset_path,source_row_id "
                "FROM feast_option_effect WHERE option_id=? ORDER BY recipient_kind,effect_ordinal",
                (row["option_id"],),
            ) if self.schema_version >= 42 else []
            category = categories.setdefault(row["category_ordinal"], {
                "category_ordinal": row["category_ordinal"], "name_zh": row["category_name_zh"], "options": [],
            })
            category["options"].append(row)
        stage["option_categories"] = list(categories.values())
        return stage

    def list_feast_stages(self):
        return [self.get_feast_stage_data(row["stage_id"]) for row in self._rows(
            "SELECT stage_id FROM feast_stage ORDER BY CAST(substr(stage_id,13) AS INTEGER),stage_id")]
