# 战斗机制图鉴的可读身份、复合归属与技能伤害关系只读查询。
"""Narrow read-only queries for combat-mechanics identity and relations."""

from __future__ import annotations

from typing import Any

from .static_game_data_dao import StaticGameDataDao


class StaticCatalogMechanicsQueries(StaticGameDataDao):
    """Expose only fixed mechanics identity and relation queries."""

    def list_identity_candidates(self, *, per_kind_limit: int = 24) -> list[dict[str, Any]]:
        limit = int(per_kind_limit)
        if not 1 <= limit <= 100:
            raise ValueError("per_kind_limit 必须在 1 到 100 之间")
        queries = (
            """
            SELECT 'gameplay_ability' AS entity_kind, ability.ability_id AS entity_key,
                   ability.name_zh AS display_name, 'formal_ability' AS provider_kind,
                   'formula' AS family_hint
            FROM gameplay_ability_catalog AS ability
            JOIN skill_damage AS damage USING (ability_id)
            LEFT JOIN skill_damage_modifier AS modifier USING (damage_id)
            WHERE length(trim(ability.name_zh)) > 1
            GROUP BY ability.ability_id, ability.name_zh
            ORDER BY COUNT(modifier.damage_id) DESC, COUNT(*) DESC, ability.ability_id
            LIMIT ?
            """,
            """
            SELECT 'skill_damage' AS entity_kind, damage.damage_id AS entity_key,
                   ability.name_zh AS display_name, 'related_ability' AS provider_kind,
                   'formula' AS family_hint
            FROM skill_damage AS damage
            JOIN gameplay_ability_catalog AS ability USING (ability_id)
            LEFT JOIN skill_damage_modifier AS modifier USING (damage_id)
            WHERE length(trim(ability.name_zh)) > 1
            ORDER BY modifier.damage_id IS NULL, damage.ability_id, damage.damage_id
            LIMIT ?
            """,
            """
            SELECT 'gameplay_effect' AS entity_kind,
                   effect.gameplay_effect_id AS entity_key,
                   ability.name_zh AS display_name, 'related_ability' AS provider_kind,
                   'attributes' AS family_hint
            FROM gameplay_effect_catalog AS effect
            JOIN skill_damage AS damage
              ON damage.damage_id = effect.gameplay_effect_id
            JOIN gameplay_ability_catalog AS ability USING (ability_id)
            WHERE length(trim(ability.name_zh)) > 1
            ORDER BY damage.ability_id, damage.damage_id
            LIMIT ?
            """,
            """
            SELECT 'buff' AS entity_kind, buff.asset_path AS entity_key,
                   ability.name_zh AS display_name, 'related_ability' AS provider_kind,
                   'attributes' AS family_hint
            FROM buff_definition AS buff
            JOIN gameplay_effect_catalog AS effect
              ON buff.asset_path = substr(
                  effect.class_path, 1, instr(effect.class_path, '.') - 1
              )
            JOIN skill_damage AS damage
              ON damage.damage_id = effect.gameplay_effect_id
            JOIN gameplay_ability_catalog AS ability USING (ability_id)
            WHERE length(trim(ability.name_zh)) > 1
            ORDER BY damage.ability_id, damage.damage_id
            LIMIT ?
            """,
            """
            SELECT 'combat_effect' AS entity_kind,
                   effect.effect_definition_id AS entity_key,
                   COALESCE(character.name_zh, fork.name_zh, suit.name_zh) AS display_name,
                   'formal_owner' AS provider_kind, '' AS family_hint
            FROM combat_effect_definition AS effect
            LEFT JOIN character
              ON effect.owner_kind = 'character_awaken'
             AND CAST(substr(effect.owner_id, 1, instr(effect.owner_id, ':') - 1)
                      AS INTEGER) = character.character_id
            LEFT JOIN fork_item AS fork
              ON effect.owner_kind = 'fork_star'
             AND effect.owner_id = fork.star_pack_id
            LEFT JOIN equipment_suit AS suit
              ON effect.owner_kind = 'equipment_suit'
             AND effect.owner_id = suit.suit_id
            WHERE COALESCE(character.name_zh, fork.name_zh, suit.name_zh) IS NOT NULL
            ORDER BY effect.owner_kind, effect.owner_id
            LIMIT ?
            """,
            """
            SELECT 'reaction' AS entity_kind, reaction_type AS entity_key,
                   NULL AS display_name, 'mechanism_collection' AS provider_kind,
                   'reactions' AS family_hint
            FROM reaction_definition ORDER BY reaction_type LIMIT ?
            """,
        )
        rows: list[dict[str, Any]] = []
        for sql in queries:
            rows.extend(self._rows(sql, (limit,)))
        return rows

    def resolve_related_identity(
        self,
        entity_kind: str,
        entity_key: str,
    ) -> dict[str, Any] | None:
        kind = str(entity_kind)
        key = str(entity_key)
        if kind == "skill_damage":
            return self._one(
                """SELECT ability.name_zh AS display_name,
                          'related_ability' AS provider_kind
                   FROM skill_damage AS damage
                   JOIN gameplay_ability_catalog AS ability USING (ability_id)
                   WHERE damage.damage_id = ? AND length(trim(ability.name_zh)) > 1""",
                (key,),
            )
        if kind == "gameplay_effect":
            return self._one(
                """SELECT ability.name_zh AS display_name,
                          'related_ability' AS provider_kind
                   FROM gameplay_effect_catalog AS effect
                   JOIN skill_damage AS damage
                     ON damage.damage_id = effect.gameplay_effect_id
                   JOIN gameplay_ability_catalog AS ability USING (ability_id)
                   WHERE effect.gameplay_effect_id = ?
                     AND length(trim(ability.name_zh)) > 1""",
                (key,),
            )
        if kind == "buff":
            return self._one(
                """SELECT ability.name_zh AS display_name,
                          'related_ability' AS provider_kind
                   FROM buff_definition AS buff
                   JOIN gameplay_effect_catalog AS effect
                     ON buff.asset_path = substr(
                         effect.class_path, 1, instr(effect.class_path, '.') - 1
                     )
                   JOIN skill_damage AS damage
                     ON damage.damage_id = effect.gameplay_effect_id
                   JOIN gameplay_ability_catalog AS ability USING (ability_id)
                   WHERE buff.asset_path = ? AND length(trim(ability.name_zh)) > 1
                   ORDER BY damage.damage_id LIMIT 1""",
                (key,),
            )
        return None

    def resolve_owner(
        self,
        owner_kind: str,
        owner_id: str,
        effect_definition_id: str = "",
    ) -> dict[str, Any] | None:
        kind = str(owner_kind).casefold()
        composite_id = str(owner_id)
        if kind == "character_awaken":
            character_id, separator, anchor = composite_id.partition(":")
            if not separator or not character_id.isdigit():
                return None
            row = self._one(
                "SELECT name_zh AS display_name FROM character WHERE character_id = ?",
                (int(character_id),),
            )
            if row is not None:
                row.update(domain_key="character", record_id=character_id,
                           relation_kind="owner", anchor=anchor)
            return row
        if kind == "fork_star":
            pack_id = composite_id
            anchor = str(effect_definition_id).rpartition(":")[2]
            row = self._one(
                """SELECT fork_id AS record_id, name_zh AS display_name
                   FROM fork_item WHERE star_pack_id = ?""",
                (pack_id,),
            )
            if row is not None:
                row.update(domain_key="fork", relation_kind="owner", anchor=anchor)
            return row
        if kind == "equipment_suit":
            suit_id = composite_id
            anchor = str(effect_definition_id).rpartition(":")[2]
            row = self._one(
                "SELECT name_zh AS display_name FROM equipment_suit WHERE suit_id = ?",
                (suit_id,),
            )
            if row is not None:
                row.update(domain_key="equipment", record_id=suit_id,
                           relation_kind="suit", anchor=anchor)
            return row
        return None

    def list_additional_relations(
        self,
        entity_kind: str,
        entity_key: str,
    ) -> list[dict[str, Any]]:
        kind = str(entity_kind)
        key = str(entity_key)
        if kind == "gameplay_ability":
            return self._rows(
                """SELECT '查看正式伤害项' AS label,
                          'skill_damage' AS target_kind, damage_id AS target_key
                   FROM skill_damage WHERE ability_id = ? ORDER BY damage_id""",
                (key,),
            )
        if kind == "skill_damage":
            return self._rows(
                """SELECT label, target_kind, target_key FROM (
                       SELECT '查看来源技能' AS label,
                              'gameplay_ability' AS target_kind,
                              ability_id AS target_key, 0 AS ordinal
                       FROM skill_damage WHERE damage_id = ? AND ability_id IS NOT NULL
                       UNION ALL
                       SELECT '查看同名 Gameplay Effect', 'gameplay_effect',
                              effect.gameplay_effect_id, 1
                       FROM gameplay_effect_catalog AS effect
                       WHERE effect.gameplay_effect_id = ?
                       UNION ALL
                       SELECT '查看正式 Buff 定义', 'buff', buff.asset_path, 2
                       FROM gameplay_effect_catalog AS effect
                       JOIN buff_definition AS buff
                         ON buff.asset_path = substr(
                             effect.class_path, 1, instr(effect.class_path, '.') - 1
                         )
                       WHERE effect.gameplay_effect_id = ?
                   ) ORDER BY ordinal""",
                (key, key, key),
            )
        if kind == "gameplay_effect":
            return self._rows(
                """SELECT label, target_kind, target_key FROM (
                       SELECT '查看正式伤害项' AS label,
                              'skill_damage' AS target_kind,
                              damage.damage_id AS target_key, 0 AS ordinal
                       FROM skill_damage AS damage WHERE damage.damage_id = ?
                       UNION ALL
                       SELECT '查看正式 Buff 定义', 'buff', buff.asset_path, 1
                       FROM gameplay_effect_catalog AS effect
                       JOIN buff_definition AS buff
                         ON buff.asset_path = substr(
                             effect.class_path, 1, instr(effect.class_path, '.') - 1
                         )
                       WHERE effect.gameplay_effect_id = ?
                   ) ORDER BY ordinal""",
                (key, key),
            )
        if kind == "buff":
            return self._rows(
                """SELECT '查看正式伤害项' AS label,
                          'skill_damage' AS target_kind,
                          damage.damage_id AS target_key
                   FROM buff_definition AS buff
                   JOIN gameplay_effect_catalog AS effect
                     ON buff.asset_path = substr(
                         effect.class_path, 1, instr(effect.class_path, '.') - 1
                     )
                   JOIN skill_damage AS damage
                     ON damage.damage_id = effect.gameplay_effect_id
                   WHERE buff.asset_path = ? ORDER BY damage.damage_id""",
                (key,),
            )
        return []

    def owner_resolution_counts(self) -> dict[str, int]:
        counts = {"character_awaken": 0, "fork_star": 0, "equipment_suit": 0}
        for row in self._rows(
            """SELECT owner_kind, owner_id, effect_definition_id
               FROM combat_effect_definition"""
        ):
            if self.resolve_owner(
                str(row["owner_kind"]),
                str(row["owner_id"]),
                str(row["effect_definition_id"]),
            ):
                counts[str(row["owner_kind"])] += 1
        return counts

    def skill_relation_counts(self) -> dict[str, int]:
        row = self._one(
            """SELECT
                   COUNT(DISTINCT CASE WHEN length(trim(ability.name_zh)) > 1
                                      THEN damage.ability_id END)
                       AS readable_abilities,
                   SUM(CASE WHEN length(trim(ability.name_zh)) > 1
                            THEN 1 ELSE 0 END) AS named_damage_items,
                   SUM(CASE WHEN length(trim(ability.name_zh)) > 1
                                 AND effect.gameplay_effect_id IS NOT NULL
                            THEN 1 ELSE 0 END) AS named_gameplay_effects,
                   SUM(CASE WHEN length(trim(ability.name_zh)) > 1
                                 AND buff.asset_path IS NOT NULL
                            THEN 1 ELSE 0 END) AS named_buffs,
                   SUM(CASE WHEN modifier.damage_id IS NOT NULL
                            THEN 1 ELSE 0 END) AS formal_modifiers
               FROM skill_damage AS damage
               LEFT JOIN gameplay_ability_catalog AS ability USING (ability_id)
               LEFT JOIN gameplay_effect_catalog AS effect
                 ON effect.gameplay_effect_id = damage.damage_id
               LEFT JOIN buff_definition AS buff
                 ON buff.asset_path = substr(
                     effect.class_path, 1, instr(effect.class_path, '.') - 1
                 )
               LEFT JOIN skill_damage_modifier AS modifier USING (damage_id)"""
        ) or {}
        return {key: int(value or 0) for key, value in row.items()}


__all__ = ["StaticCatalogMechanicsQueries"]
