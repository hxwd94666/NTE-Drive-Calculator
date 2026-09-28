# 将角色觉醒、面板成长与技能目录查询从静态库主 DAO 中拆出。
"""Read-only character growth queries for the static database."""

from __future__ import annotations

import json
from typing import Any

from .protocols import StaticDataDaoMixinHost


class StaticGameDataCharacterGrowthQueriesMixin(StaticDataDaoMixinHost):
    """Expose awaken effects, official panel growth and the skill catalogue."""

    def list_character_shape_bonuses(self) -> dict[int, dict[str, Any]]:
        """批量返回全部官方角色的额外形状规则，供逐角色装配前一次性读取。"""

        properties: dict[str, list[dict[str, Any]]] = {}
        for row in self._rows(
            """
            SELECT p.logical_character_key, p.property_id, p.display_value, p.ordinal,
                   a.display_name_zh, a.filter_name_zh, a.show_percent
            FROM logical_character_shape_bonus_property AS p
            JOIN equipment_attribute AS a ON a.attribute_id = p.property_id
            ORDER BY p.logical_character_key, p.ordinal
            """
        ):
            logical_key = str(row.pop("logical_character_key"))
            properties.setdefault(logical_key, []).append(row)
        bonuses: dict[int, dict[str, Any]] = {}
        for row in self._rows(
            """
            SELECT a.character_id, a.logical_character_key,
                   b.representative_character_id, b.shape_label,
                   b.shape_grid_count, b.source_kind
            FROM character_annotation AS a
            JOIN logical_character_shape_bonus AS b
              ON b.logical_character_key = a.logical_character_key
            """
        ):
            row["properties"] = properties.get(str(row["logical_character_key"]), [])
            bonuses[int(row["character_id"])] = row
        return bonuses

    def list_character_awaken_effects(self, character_id: int) -> list[dict[str, Any]]:
        """返回角色六觉与三/六觉共鸣，含可直接应用的技能等级加成。"""

        effects = self._rows(
            """
            SELECT character_id, effect_id, ordinal, awaken_type, title_zh,
                   title_text_table, title_text_key, description_zh,
                   description_text_table, description_text_key, icon_path,
                   modify_data_json, gameplay_effect_ids_json, source_row_id
            FROM character_awaken_effect
            WHERE character_id = ?
            ORDER BY ordinal
            """,
            (character_id,),
        )
        bonuses_by_effect: dict[str, list[dict[str, Any]]] = {}
        for bonus in self._rows(
            """
            SELECT effect_id, ordinal, skill_id, level_delta
            FROM character_awaken_skill_level_bonus
            WHERE character_id = ?
            ORDER BY effect_id, ordinal
            """,
            (character_id,),
        ):
            effect_id = bonus.pop("effect_id")
            bonuses_by_effect.setdefault(effect_id, []).append(bonus)
        for effect in effects:
            effect["modify_data"] = json.loads(effect.pop("modify_data_json"))
            effect["gameplay_effect_ids"] = json.loads(effect.pop("gameplay_effect_ids_json"))
            effect["skill_level_bonuses"] = bonuses_by_effect.get(effect["effect_id"], [])
            effect["description_damage_entries"] = [
                damage
                for damage_id in effect["gameplay_effect_ids"]
                if (damage := self.get_skill_damage(str(damage_id))) is not None
            ]
        return effects

    def get_character_panel_growth(
        self, character_id: int, level: int, breakthrough_stage: int
    ) -> dict[str, Any] | None:
        """按角色、等级和已突破阶段返回官方基础生命、攻击和防御。"""

        return self._one(
            """
            SELECT character_id, level, breakthrough_stage, state,
                   hp_base, atk_base, def_base,
                   player_pack_source_row_id, level_modify_source_row_id,
                   breakthrough_modify_source_row_id
            FROM character_panel_growth
            WHERE character_id = ? AND level = ? AND breakthrough_stage = ?
            """,
            (character_id, level, breakthrough_stage),
        )

    def list_character_panel_growth(self, character_id: int) -> list[dict[str, Any]]:
        """返回角色全部官方等级/突破面板，供角色页选择而非复制数值。"""

        return self._rows(
            """
            SELECT character_id, level, breakthrough_stage, state,
                   hp_base, atk_base, def_base,
                   player_pack_source_row_id, level_modify_source_row_id,
                   breakthrough_modify_source_row_id
            FROM character_panel_growth
            WHERE character_id = ?
            ORDER BY level, breakthrough_stage
            """,
            (character_id,),
        )

    def list_character_skills(self, character_id: int) -> list[dict[str, Any]]:
        """返回角色技能目录及每一级对应的突破、觉醒和材料要求。"""

        skills = self._rows(
            """
            SELECT character_id, skill_id, ability_type, ability_index,
                   show_detail_info, gameplay_tag, gameplay_effect_path,
                   reapply_after_revive, ability_source_row_id, effect_source_row_id
            FROM character_skill
            WHERE character_id = ?
            ORDER BY ability_index, skill_id
            """,
            (character_id,),
        )
        levels_by_skill: dict[str, list[dict[str, Any]]] = {}
        for level in self._rows(
            """
            SELECT skill_id, level, required_breakthrough_stage,
                   required_awaken_level, cost_items_json
            FROM character_skill_level
            WHERE character_id = ?
            ORDER BY skill_id, level
            """,
            (character_id,),
        ):
            skill_id = level.pop("skill_id")
            level["cost_items"] = json.loads(level.pop("cost_items_json"))
            levels_by_skill.setdefault(skill_id, []).append(level)
        for skill in skills:
            skill["show_detail_info"] = bool(skill["show_detail_info"])
            skill["reapply_after_revive"] = bool(skill["reapply_after_revive"])
            skill["levels"] = levels_by_skill.get(skill["skill_id"], [])
            skill["damage_entries"] = self._rows(
                """
                SELECT d.damage_id, d.damage_type, d.charge_add, d.unbal_value,
                       d.heterochrome_add, d.damage_source_category, d.fixed_crit_rate,
                       d.atk_rate_base_json, d.def_rate_base_json, d.hp_rate_base_json,
                       d.story_balance_ge_rate, d.attack_break_level,
                       d.override_breakable_damage, d.breakable_damage,
                       d.override_breakable_impulse, d.breakable_impulse,
                       d.override_vehicle_breakable_impulse,
                       d.vehicle_breakable_impulse, d.source_row_id,
                       m.atk_rate_base_coefficient AS modifier_atk_rate_base_coefficient,
                       m.source_row_id AS modifier_source_row_id
                FROM skill_damage AS d
                LEFT JOIN skill_damage_modifier AS m USING (damage_id)
                WHERE d.ability_id = ?
                ORDER BY d.damage_id
                """,
                (skill["skill_id"],),
            )
            for damage in skill["damage_entries"]:
                for key in ("atk_rate_base", "def_rate_base", "hp_rate_base"):
                    damage[key] = json.loads(damage.pop(f"{key}_json"))
                for key in (
                    "override_breakable_damage",
                    "override_breakable_impulse",
                    "override_vehicle_breakable_impulse",
                ):
                    damage[key] = bool(damage[key])
        return skills
