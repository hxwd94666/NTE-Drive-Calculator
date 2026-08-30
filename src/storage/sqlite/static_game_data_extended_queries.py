# 提供静态游戏数据库中的装备方案、战斗和来源扩展查询。
"""标准化静态游戏数据库的只读访问层。"""

from __future__ import annotations

import json
from typing import Any

from .protocols import StaticDataDaoMixinHost


def _official_pack_key(value: object) -> str:
    """Normalize official pack identifiers without changing stored source facts."""

    return str(value or "").casefold()


class StaticGameDataExtendedQueriesMixin(StaticDataDaoMixinHost):
    def list_forks(self) -> list[dict[str, Any]]:
        rows = self._rows(
            """
            SELECT f.fork_id, f.name_zh, f.name_text_table, f.name_text_key,
                   f.description_zh, f.quality, f.fork_type_id,
                   t.name_zh AS fork_type_name_zh, f.raw_group_type,
                   f.upgrade_pack_id, f.breakthrough_pack_id, f.star_pack_id,
                   f.max_breakthrough, f.max_star, f.icon_path, f.card_path,
                   f.painting_path, f.exclusive_character_ids_json,
                   f.source_row_id
            FROM fork_item AS f
            LEFT JOIN fork_type AS t USING (fork_type_id)
            ORDER BY f.fork_id
            """
        )
        for row in rows:
            row["exclusive_character_ids"] = json.loads(
                row.pop("exclusive_character_ids_json")
            )
        return rows

    def list_fork_templates(self) -> list[dict[str, Any]]:
        """返回弧盘的官方成长、突破、星级和逐项属性加成。"""
        recommendations_by_fork: dict[str, list[dict[str, Any]]] = {}
        for row in self._rows(
            """
            SELECT fork_id, character_id, ordinal, description_zh, source_kind
            FROM character_cultivation_fork_recommendation
            ORDER BY fork_id, ordinal, character_id
            """
        ):
            recommendations_by_fork.setdefault(row.pop("fork_id"), []).append(row)
        modifiers_by_pack: dict[str, list[dict[str, Any]]] = {}
        for row in self._rows(
            """
            SELECT modify_pack_id, ordinal, property_id, value, operation, sort_key
            FROM fork_modify_value
            ORDER BY modify_pack_id, ordinal
            """
        ):
            modifiers_by_pack.setdefault(
                _official_pack_key(row.pop("modify_pack_id")), []
            ).append(row)

        conditions_by_pack = {
            _official_pack_key(row["modify_pack_id"]): json.loads(
                row["conditions_json"] or "[]"
            )
            for row in self._rows(
                "SELECT modify_pack_id, conditions_json FROM fork_modify_pack"
            )
        }

        def modifiers(pack_id: str | None) -> list[dict[str, Any]]:
            if not pack_id:
                return []
            return [
                dict(row)
                for row in modifiers_by_pack.get(_official_pack_key(pack_id), [])
            ]

        upgrades_by_pack: dict[str, list[dict[str, Any]]] = {}
        for row in self._rows(
            """
            SELECT upgrade_pack_id, level, need_exp, modify_pack_id
            FROM fork_upgrade_level
            ORDER BY upgrade_pack_id, level
            """
        ):
            pack_id = row.pop("upgrade_pack_id")
            modify_pack_id = row.pop("modify_pack_id")
            row["modifiers"] = modifiers(modify_pack_id)
            row["conditions"] = conditions_by_pack.get(
                _official_pack_key(modify_pack_id), []
            )
            upgrades_by_pack.setdefault(_official_pack_key(pack_id), []).append(row)

        breakthroughs_by_pack: dict[str, list[dict[str, Any]]] = {}
        for row in self._rows(
            """
            SELECT breakthrough_pack_id, stage, max_fork_level, need_items,
                   need_gold, modify_pack_id
            FROM fork_breakthrough
            ORDER BY breakthrough_pack_id, stage
            """
        ):
            pack_id = row.pop("breakthrough_pack_id")
            modify_pack_id = row.pop("modify_pack_id")
            row["modifiers"] = modifiers(modify_pack_id)
            row["conditions"] = conditions_by_pack.get(
                _official_pack_key(modify_pack_id), []
            )
            breakthroughs_by_pack.setdefault(
                _official_pack_key(pack_id), []
            ).append(row)

        parameters_by_star: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for row in self._rows(
            """
            SELECT p.star_pack_id, p.star_level, p.ordinal, p.name_id,
                   p.is_percent, v.value
            FROM fork_star_parameter AS p
            LEFT JOIN fork_refinement_parameter_value AS v
              ON v.name_id = p.name_id
             AND v.refinement_level = p.star_level
            ORDER BY p.star_pack_id, p.star_level, p.ordinal
            """
        ):
            key = (row.pop("star_pack_id"), int(row.pop("star_level")))
            row["is_percent"] = bool(row["is_percent"])
            parameters_by_star.setdefault(
                (_official_pack_key(key[0]), key[1]), []
            ).append(row)

        stars_by_pack: dict[str, list[dict[str, Any]]] = {}
        for row in self._rows(
            """
            SELECT star_pack_id, star_level, title_zh, description_zh,
                   need_gold, buffs_json
            FROM fork_star_level
            ORDER BY star_pack_id, star_level
            """
        ):
            pack_id = row.pop("star_pack_id")
            star_level = int(row["star_level"])
            row["buffs"] = json.loads(row.pop("buffs_json") or "[]")
            row["parameters"] = parameters_by_star.get(
                (_official_pack_key(pack_id), star_level), []
            )
            stars_by_pack.setdefault(_official_pack_key(pack_id), []).append(row)

        templates: list[dict[str, Any]] = []
        for fork in self.list_forks():
            template = dict(fork)
            template["upgrade_levels"] = upgrades_by_pack.get(
                _official_pack_key(template.get("upgrade_pack_id")), []
            )
            template["breakthroughs"] = breakthroughs_by_pack.get(
                _official_pack_key(template.get("breakthrough_pack_id")), []
            )
            template["star_levels"] = stars_by_pack.get(
                _official_pack_key(template.get("star_pack_id")), []
            )
            template["cultivation_recommendations"] = recommendations_by_fork.get(
                str(template.get("fork_id") or ""), []
            )
            templates.append(template)
        return templates

    def get_equipment_plan(self, character_id: int) -> dict[str, Any] | None:
        plan = self._one(
            """
            SELECT p.character_id, c.name_zh AS character_name_zh,
                   p.core_item_id, core.name_zh AS core_name_zh,
                   p.core_level, p.module_level, p.reference_score,
                   p.background_path, p.character_image_path, p.source_row_id
            FROM equipment_plan AS p
            JOIN character AS c USING (character_id)
            JOIN equipment_item AS core ON core.item_id = p.core_item_id
            WHERE p.character_id = ?
            """,
            (character_id,),
        )
        if plan is None:
            return None
        plan["core_attribute_ids"] = [
            row["attribute_id"]
            for row in self._rows(
                """
                SELECT attribute_id FROM equipment_plan_core_attribute
                WHERE character_id = ? ORDER BY ordinal
                """,
                (character_id,),
            )
        ]
        plan["recommended_attribute_ids"] = [
            row["attribute_id"]
            for row in self._rows(
                """
                SELECT attribute_id FROM equipment_plan_recommended_attribute
                WHERE character_id = ? ORDER BY ordinal
                """,
                (character_id,),
            )
        ]
        plan["cells"] = self._rows(
            """
            SELECT row, column, anchor_item_id FROM equipment_plan_cell
            WHERE character_id = ? ORDER BY row, column
            """,
            (character_id,),
        )
        plan["module_item_ids"] = [
            row["item_id"]
            for row in self._rows(
                """
                SELECT item_id FROM equipment_plan_module
                WHERE character_id = ? ORDER BY ordinal
                """,
                (character_id,),
            )
        ]
        return plan

    def get_character_cultivation_guide(
        self, character_id: int
    ) -> dict[str, Any] | None:
        guide = self._one(
            """
            SELECT character_id, display_text, s_score, a_score, icon_path,
                   recommend_attribute_jump_id, role_sex_change, source_row_id
            FROM character_cultivation_guide
            WHERE character_id = ?
            """,
            (int(character_id),),
        )
        if guide is None:
            return None
        guide["display_text"] = bool(guide["display_text"])
        guide["role_sex_change"] = bool(guide["role_sex_change"])
        guide["fork_recommendations"] = self._rows(
            """
            SELECT ordinal, fork_id, description_zh, source_kind
            FROM character_cultivation_fork_recommendation
            WHERE character_id = ? ORDER BY ordinal
            """,
            (int(character_id),),
        )
        guide["attribute_recommendations"] = self._rows(
            """
            SELECT recommendation.ordinal, recommendation.property_id,
                   attribute.display_name_zh, attribute.show_percent
            FROM character_cultivation_attribute_recommendation AS recommendation
            LEFT JOIN equipment_attribute AS attribute
              ON attribute.attribute_id = recommendation.property_id
            WHERE recommendation.character_id = ?
            ORDER BY recommendation.ordinal
            """,
            (int(character_id),),
        )
        stages = self._rows(
            """
            SELECT stage_ordinal, character_level, fork_level, core_item_id,
                   core_level, equipment_level
            FROM character_cultivation_stage
            WHERE character_id = ? ORDER BY stage_ordinal
            """,
            (int(character_id),),
        )
        for stage in stages:
            stage["skills"] = self._rows(
                """
                SELECT sex_kind, ordinal, ability_id, recommended_level
                FROM character_cultivation_stage_skill
                WHERE character_id = ? AND stage_ordinal = ?
                ORDER BY sex_kind, ordinal
                """,
                (int(character_id), int(stage["stage_ordinal"])),
            )
        guide["stages"] = stages
        return guide

    def get_gameplay_ability(self, ability_id: str) -> dict[str, Any] | None:
        ability = self._one(
            """
            SELECT ability_id, name_zh, name_text_table, name_text_key,
                   icon_path, extended_icon_path, gameplay_ability_path,
                   is_stolen, source_row_id
            FROM gameplay_ability_catalog WHERE ability_id = ?
            """,
            (str(ability_id),),
        )
        if ability is None:
            return None
        ability["is_stolen"] = bool(ability["is_stolen"])
        descriptions = self._rows(
            """
            SELECT ordinal, description_type, title_zh, description_zh,
                   description_text_table, description_text_key,
                   short_description_zh, unlock_id, unlock_description_zh,
                   replacement_values_json
            FROM gameplay_ability_description
            WHERE ability_id = ? ORDER BY ordinal
            """,
            (str(ability_id),),
        )
        for row in descriptions:
            row["replacement_values"] = json.loads(
                row.pop("replacement_values_json") or "[]"
            )
        ability["descriptions"] = descriptions
        hints = self._rows(
            """
            SELECT ordinal, name_id, description_zh, value_description_zh,
                   global_curve_id, source_type, damage_effect_ids_json,
                   defense_effect_ids_json, health_effect_ids_json
            FROM gameplay_ability_level_hint
            WHERE ability_id = ? ORDER BY ordinal
            """,
            (str(ability_id),),
        )
        for row in hints:
            for field in (
                "damage_effect_ids",
                "defense_effect_ids",
                "health_effect_ids",
            ):
                row[field] = json.loads(row.pop(f"{field}_json") or "[]")
        ability["level_hints"] = hints
        return ability

    def list_gameplay_ability_names(self) -> list[dict[str, Any]]:
        """Return stable ability IDs and official Chinese display names."""

        return self._rows(
            """
            SELECT ability_id, name_zh
            FROM gameplay_ability_catalog ORDER BY ability_id
            """
        )

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
        """Return player-levelled abilities whose official hint owns one damage GE."""

        rows = self._rows(
            """
            SELECT DISTINCT hint.ability_id
            FROM character_cultivation_stage_skill AS stage_skill
            JOIN gameplay_ability_level_hint AS hint
              ON hint.ability_id = stage_skill.ability_id
            JOIN json_each(hint.damage_effect_ids_json) AS damage
            WHERE stage_skill.character_id = ? AND damage.value = ?
            ORDER BY hint.ability_id
            """,
            (int(character_id), str(damage_id)),
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

    def list_gameplay_effects(self) -> list[dict[str, Any]]:
        """Return the stable GE index-to-ID catalog for evidence fallback."""

        return self._rows(
            """
            SELECT gameplay_effect_index, gameplay_effect_id, class_path
            FROM gameplay_effect_catalog ORDER BY gameplay_effect_index
            """
        )

    def get_gameplay_effect(
        self,
        *,
        gameplay_effect_index: int | None = None,
        gameplay_effect_id: str | None = None,
    ) -> dict[str, Any] | None:
        if gameplay_effect_index is not None:
            return self._one(
                """
                SELECT gameplay_effect_index, gameplay_effect_id, class_path,
                       source_row_id
                FROM gameplay_effect_catalog WHERE gameplay_effect_index = ?
                """,
                (int(gameplay_effect_index),),
            )
        if gameplay_effect_id:
            return self._one(
                """
                SELECT gameplay_effect_index, gameplay_effect_id, class_path,
                       source_row_id
                FROM gameplay_effect_catalog WHERE gameplay_effect_id = ?
                """,
                (str(gameplay_effect_id),),
            )
        return None

    def find_monster_catalog(
        self,
        identifier: str,
        *,
        alias_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized = str(identifier)
        parameters: list[Any] = [normalized, normalized]
        alias_filter = ""
        if alias_kind is not None:
            alias_filter = " AND alias.alias_kind = ?"
            parameters.append(str(alias_kind))
        return self._rows(
            """
            SELECT DISTINCT monster.monster_manual_id, monster.sort_order,
                   monster.name_zh, monster.enemy_type, monster.image_path,
                   monster.world_image_path, monster.place_zh,
                   monster.discovered_description_zh,
                   monster.undiscovered_description_zh, monster.drop_id,
                   monster.stamina_cost, monster.trace_type,
                   monster.map_icon_id, monster.quest_id, monster.source_row_id
            FROM monster_catalog AS monster
            LEFT JOIN monster_identifier_alias AS alias
              USING (monster_manual_id)
            WHERE (monster.monster_manual_id = ? OR alias.alias_value = ?)
            """
            + alias_filter
            + " ORDER BY monster.sort_order, monster.monster_manual_id",
            parameters,
        )

    def list_combat_effect_definitions(
        self,
        *,
        owner_kind: str | None = None,
        owner_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if owner_kind is not None:
            clauses.append("owner_kind = ?")
            parameters.append(str(owner_kind))
        if owner_id is not None:
            clauses.append("owner_id = ?")
            parameters.append(str(owner_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._rows(
            """
            SELECT effect_definition_id, owner_kind, owner_id, effect_kind,
                   activation_kind, description_zh, parameters_json,
                   formula_version, source_row_id
            FROM combat_effect_definition
            """
            + where
            + " ORDER BY owner_kind, owner_id, effect_definition_id",
            parameters,
        )
        for row in rows:
            row["parameters"] = json.loads(row.pop("parameters_json") or "{}")
        return rows

    def list_equipment_plans(self) -> list[dict[str, Any]]:
        """Return every official equipment plan without per-role query fan-out.

        The weighted-allocation page needs the full role directory.  Keeping
        this as a batch query prevents its initial render from issuing the five
        plan queries once for every character.
        """

        plans = self._rows(
            """
            SELECT p.character_id, c.name_zh AS character_name_zh,
                   p.core_item_id, core.name_zh AS core_name_zh,
                   p.core_level, p.module_level, p.reference_score,
                   p.background_path, p.character_image_path, p.source_row_id
            FROM equipment_plan AS p
            JOIN character AS c USING (character_id)
            JOIN equipment_item AS core ON core.item_id = p.core_item_id
            ORDER BY p.character_id
            """
        )
        by_character_id = {
            int(plan["character_id"]): plan for plan in plans
        }
        collections = (
            (
                "core_attribute_ids",
                "equipment_plan_core_attribute",
                "attribute_id",
            ),
            (
                "recommended_attribute_ids",
                "equipment_plan_recommended_attribute",
                "attribute_id",
            ),
            ("module_item_ids", "equipment_plan_module", "item_id"),
        )
        for key, table, value_column in collections:
            for plan in plans:
                plan[key] = []
            for row in self._rows(
                f"SELECT character_id, {value_column} FROM {table} "
                "ORDER BY character_id, ordinal"
            ):
                target_plan = by_character_id.get(int(row["character_id"]))
                if target_plan is not None:
                    target_plan[key].append(row[value_column])
        for plan in plans:
            plan["cells"] = []
        for row in self._rows(
            """
            SELECT character_id, row, column, anchor_item_id
            FROM equipment_plan_cell
            ORDER BY character_id, row, column
            """
        ):
            target_plan = by_character_id.get(int(row["character_id"]))
            if target_plan is not None:
                target_plan["cells"].append({
                    "row": row["row"],
                    "column": row["column"],
                    "anchor_item_id": row["anchor_item_id"],
                })
        return plans

    def get_character_default_suit(self, character_id: int) -> dict[str, Any] | None:
        """返回官方配装图纸中卡带所属的默认套装。"""

        return self._one(
            """
            SELECT core.suit_id, suit.name_zh AS suit_name_zh
            FROM equipment_plan AS plan
            JOIN equipment_item AS core ON core.item_id = plan.core_item_id
            JOIN equipment_suit AS suit ON suit.suit_id = core.suit_id
            WHERE plan.character_id = ?
            """,
            (int(character_id),),
        )

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

    def get_combat_level_curve(self, curve_id: str) -> dict[str, Any] | None:
        curve = self._one(
            """
            SELECT curve_id, damage_kind, reaction_type, source_effect_id,
                   interpolation_mode, mapping_status, source_row_id
            FROM combat_level_curve WHERE curve_id = ?
            """,
            (curve_id,),
        )
        if curve is not None:
            curve["points"] = self._rows(
                """
                SELECT ordinal, character_level, source_tier, value
                FROM combat_level_curve_point WHERE curve_id = ? ORDER BY ordinal
                """,
                (curve_id,),
            )
        return curve

    def get_topple_level_multiplier(self, character_level: float) -> float | None:
        point = self._one(
            """
            SELECT value FROM combat_level_curve_point
            WHERE curve_id = 'topple:character_level' AND character_level = ?
            """,
            (float(character_level),),
        )
        return None if point is None else float(point["value"])

    def get_reaction_damage_curve(self, effect_id: str) -> dict[str, Any] | None:
        return self.get_combat_level_curve(f"reaction:{str(effect_id).strip()}")

    def list_reaction_definitions(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT reaction_type, element_type_1, element_type_2,
                   default_damage_effect_id, source_row_id
            FROM reaction_definition ORDER BY reaction_type
            """
        )

    def list_combat_effect_constants(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT constant_id, source_time, value, unit, description_zh, source_row_id
            FROM combat_effect_constant ORDER BY constant_id
            """
        )

    def get_enemy_combat_profile(
        self, profile_set: str, pack_id: str
    ) -> dict[str, Any] | None:
        """返回普通或 999 夜属性包及分元素抗性。"""
        if profile_set not in ("standard", "night_999"):
            raise ValueError("profile_set 必须是 standard 或 night_999")
        profile = self._one(
            """
            SELECT profile_set, pack_id, defense_base, defense_up, defense_add,
                   defense_ignore, topple_limit, topple_accrue_efficiency,
                   topple_anti_accrue_efficiency, topple_bonus,
                   topple_reduce_natural, topple_reduce_reset, source_row_id,
                   health_base, health_up, health_add
            FROM enemy_combat_profile WHERE profile_set = ? AND pack_id = ?
            """,
            (profile_set, str(pack_id).strip()),
        )
        if profile is not None:
            profile["resistances"] = {
                row["damage_type"]: {
                    "resistance_base": row["resistance_base"],
                    "immunity": row["immunity"],
                }
                for row in self._rows(
                    """
                    SELECT damage_type, resistance_base, immunity
                    FROM enemy_element_resistance
                    WHERE profile_set = ? AND pack_id = ? ORDER BY damage_type
                    """,
                    (profile_set, profile["pack_id"]),
                )
            }
        return profile

    def get_monster_instance_profile(
        self, static_table: str, monster_id: str
    ) -> dict[str, Any] | None:
        binding = self._one(
            """
            SELECT static_table, monster_id, monster_level, default_profile_set,
                   default_pack_id, online_ratio_id, source_row_id
            FROM monster_instance_profile WHERE static_table = ? AND monster_id = ?
            """,
            (str(static_table).strip(), str(monster_id).strip()),
        )
        if binding is not None:
            binding["variants"] = self._rows(
                """
                SELECT variant_kind, threshold_level, profile_set, pack_id
                FROM monster_instance_profile_variant
                WHERE static_table = ? AND monster_id = ?
                ORDER BY variant_kind, threshold_level
                """,
                (binding["static_table"], binding["monster_id"]),
            )
        return binding

    def get_abyss_level_monsters(
        self, level_config_id: str, level_id: int
    ) -> dict[str, Any] | None:
        """返回一个 Abyss 关卡的波次、怪物及属性包来源。"""
        level = self._one(
            """
            SELECT level_config_id, level_id, abyss_id, name_zh, source_row_id
            FROM abyss_level WHERE level_config_id = ? AND level_id = ?
            """,
            (str(level_config_id).strip(), int(level_id)),
        )
        if level is not None:
            level["spawns"] = self._rows(
                """
                SELECT s.fight_stage, s.spawn_ordinal, s.wave, s.monster_pool_id,
                       s.next_spawn_type, s.spawn_time, s.source_row_id,
                       p.monster_ordinal, p.monster_class_path, p.monster_count,
                       p.monster_level, p.attribute_profile_set, p.attribute_pack_id,
                       p.attribute_source_row_id
                FROM abyss_level_monster_spawn AS s
                JOIN abyss_monster_pool_entry AS p USING (monster_pool_id)
                WHERE s.level_config_id = ? AND s.level_id = ?
                ORDER BY s.fight_stage, s.spawn_ordinal, p.monster_ordinal
                """,
                (level["level_config_id"], level["level_id"]),
            )
        return level

    def get_source_payload(self, relative_path: str, row_key: str) -> Any | None:
        row = self._one(
            """
            SELECT r.payload_json
            FROM source_row AS r
            JOIN source_file AS f USING (source_file_id)
            WHERE f.relative_path = ? AND r.row_key = ?
            """,
            (relative_path, str(row_key)),
        )
        if row is None or row["payload_json"] is None:
            return None
        return json.loads(row["payload_json"])

    def get_character_likeability_bonus(
        self,
        character_id: int,
    ) -> dict[str, Any] | None:
        bonus = self._one(
            """
            SELECT character_id, required_level, modify_data_id,
                   source_row_id, modifier_source_row_id
            FROM character_likeability_bonus
            WHERE character_id = ?
            """,
            (int(character_id),),
        )
        if bonus is None:
            return None
        bonus["properties"] = self._rows(
            """
            SELECT ordinal, property_id, value, modifier_operation,
                   source_row_id
            FROM character_likeability_bonus_property
            WHERE character_id = ? ORDER BY ordinal
            """,
            (int(character_id),),
        )
        return bonus
