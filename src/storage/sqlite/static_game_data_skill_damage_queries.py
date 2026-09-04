# 提供静态技能伤害、正式归属和技能等级继承关系查询。
"""Read-only queries for normalized skill-damage evidence."""

from __future__ import annotations

import json
from typing import Any


class StaticGameDataSkillDamageQueriesMixin:
    """Expose skill-damage rows without coupling them to unrelated catalogs."""

    def list_skill_damage_name_bindings(self) -> list[dict[str, Any]]:
        """Return stable GameplayEffect-to-ability identities for presentation."""

        return self._rows(
            """
            SELECT damage_id, ability_id, damage_type
            FROM skill_damage ORDER BY damage_id
            """
        )

    def list_skill_level_ability_candidates(
        self,
        character_id: int,
        damage_id: str,
    ) -> list[str]:
        """Return abilities that own a damage GE directly or via FollowSkillClass."""

        rows = self._rows(
            """
            WITH candidate(ability_id) AS (
                SELECT hint.ability_id
                FROM character_cultivation_stage_skill AS stage_skill
                JOIN gameplay_ability_level_hint AS hint
                  ON hint.ability_id = stage_skill.ability_id
                JOIN json_each(hint.damage_effect_ids_json) AS damage
                WHERE stage_skill.character_id = ? AND damage.value = ?

                UNION

                SELECT followed_ability.asset_name
                FROM skill_damage AS damage
                JOIN combat_blueprint_asset AS source_ability
                  ON source_ability.asset_name = damage.ability_id
                 AND source_ability.character_id = ?
                JOIN combat_blueprint_reference AS followed
                  ON followed.source_asset_path = source_ability.asset_path
                 AND followed.property_path LIKE '%.Properties.FollowSkillClass'
                JOIN combat_blueprint_asset AS followed_ability
                  ON followed_ability.asset_path = followed.target_asset_path
                JOIN character_cultivation_stage_skill AS stage_skill
                  ON stage_skill.character_id = ?
                 AND stage_skill.ability_id = followed_ability.asset_name
                WHERE damage.damage_id = ?
            )
            SELECT DISTINCT ability_id
            FROM candidate
            ORDER BY ability_id
            """,
            (
                int(character_id),
                str(damage_id),
                int(character_id),
                int(character_id),
                str(damage_id),
            ),
        )
        return [str(row["ability_id"]) for row in rows]

    def list_skill_damage_owner_character_ids(self, damage_id: str) -> list[int]:
        """Return formal character owners of one imported skill-damage row."""

        rows = self._rows(
            """
            SELECT DISTINCT owner.character_id
            FROM skill_damage AS damage
            JOIN (
                SELECT character_id, skill_id AS ability_id
                FROM character_skill
                UNION
                SELECT character_id, ability_id
                FROM character_combat_ability_binding
            ) AS owner ON owner.ability_id = damage.ability_id
            WHERE damage.damage_id = ?
            ORDER BY owner.character_id
            """,
            (str(damage_id),),
        )
        return [int(row["character_id"]) for row in rows]

    def get_skill_damage(self, damage_id: str) -> dict[str, Any] | None:
        """按官方伤害记录 ID 返回 v7 原始倍率数组和修正规则。"""

        damage = self._one(
            """
            SELECT d.*, m.atk_rate_base_coefficient AS modifier_atk_rate_base_coefficient,
                   m.source_row_id AS modifier_source_row_id
            FROM skill_damage AS d
            LEFT JOIN skill_damage_modifier AS m USING (damage_id)
            WHERE d.damage_id = ?
            """,
            (str(damage_id).strip(),),
        )
        if damage is None:
            return None
        for key in ("atk_rate_base", "def_rate_base", "hp_rate_base"):
            damage[key] = json.loads(damage.pop(f"{key}_json"))
        for key in (
            "override_breakable_damage",
            "override_breakable_impulse",
            "override_vehicle_breakable_impulse",
        ):
            damage[key] = bool(damage[key])
        return damage
