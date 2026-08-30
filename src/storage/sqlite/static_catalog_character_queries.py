# 游戏资料库角色域的只读、参数化 SQLite 查询。
"""Narrow release-static queries for the character catalog domain."""

from __future__ import annotations

from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


_CHARACTER_SEARCH_PREDICATE = """
(
    ? = ''
    OR CAST(c.character_id AS TEXT) LIKE ? ESCAPE '\\'
    OR c.name_zh LIKE ? ESCAPE '\\'
    OR COALESCE(c.element_type, '') LIKE ? ESCAPE '\\'
    OR COALESCE(c.group_type, '') LIKE ? ESCAPE '\\'
    OR COALESCE(c.actor_path, '') LIKE ? ESCAPE '\\'
    OR EXISTS (
        SELECT 1 FROM character_skill AS skill
        WHERE skill.character_id = c.character_id
          AND (
              skill.skill_id LIKE ? ESCAPE '\\'
              OR COALESCE(skill.gameplay_tag, '') LIKE ? ESCAPE '\\'
              OR COALESCE(skill.gameplay_effect_path, '') LIKE ? ESCAPE '\\'
          )
    )
    OR EXISTS (
        SELECT 1 FROM character_combat_ability_binding AS binding
        WHERE binding.character_id = c.character_id
          AND (
              binding.ability_id LIKE ? ESCAPE '\\'
              OR binding.ability_asset_path LIKE ? ESCAPE '\\'
          )
    )
    OR EXISTS (
        SELECT 1
        FROM character_combat_ability_binding AS binding
        JOIN combat_ability_effect_binding AS effect
          ON effect.ability_asset_path = binding.ability_asset_path
        WHERE binding.character_id = c.character_id
          AND (
              effect.effect_id LIKE ? ESCAPE '\\'
              OR effect.effect_asset_path LIKE ? ESCAPE '\\'
          )
    )
    OR EXISTS (
        SELECT 1 FROM buff_definition AS buff
        WHERE buff.owner_character_id = c.character_id
          AND (
              buff.definition_id LIKE ? ESCAPE '\\'
              OR buff.asset_path LIKE ? ESCAPE '\\'
          )
    )
)
"""


def _search_parameters(query: str) -> tuple[str, ...]:
    normalized = str(query or "").strip()
    escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return (normalized, *(pattern for _index in range(14)))


class StaticCatalogCharacterQueries(StaticGameDataDao):
    """Character-only catalog reader built on the release DB read-only boundary."""

    def character_catalog_metadata(self) -> dict[str, Any]:
        row = self._one(
            """
            SELECT dataset_id, NULL AS game_version, importer_version, built_at_utc,
                   (SELECT MAX(version) FROM schema_migration) AS schema_version
            FROM dataset
            """
        )
        if row is None:
            raise RuntimeError("静态数据库缺少 dataset 元信息")
        return row

    def list_catalog_character_release_annotations(
        self,
    ) -> list[dict[str, object]]:
        """Read v30 character release facts with deterministic evidence keys."""

        rows = self._rows(
            """
            SELECT character_id, quality, quality_source_kind,
                   acquisition_type, acquisition_source_kind,
                   mainland_release_date, release_source_kind
            FROM character_release_annotation
            ORDER BY character_id
            """
        )
        evidence_rows = self._rows(
            """
            SELECT character_id, evidence_key
            FROM character_release_evidence_link
            ORDER BY character_id, field_name, ordinal, evidence_key
            """
        )
        evidence_by_character: dict[int, list[str]] = {}
        for evidence in evidence_rows:
            character_id = int(evidence["character_id"])
            evidence_by_character.setdefault(character_id, []).append(
                str(evidence["evidence_key"])
            )
        for row in rows:
            character_id = int(row["character_id"])
            row["evidence_keys"] = tuple(dict.fromkeys(
                evidence_by_character.get(character_id, ())
            ))
        return rows

    def count_catalog_characters(self, query: str = "") -> int:
        row = self._one(
            f"""
            SELECT COUNT(*) AS count
            FROM character AS c
            WHERE {_CHARACTER_SEARCH_PREDICATE}
            """,
            _search_parameters(query),
        )
        return int((row or {}).get("count", 0))

    def list_catalog_characters(
        self,
        *,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._rows(
            f"""
            SELECT c.character_id, c.name_zh, c.element_type, c.group_type,
                   c.actor_path, c.mainland_show_time,
                   annotation.logical_character_key,
                   annotation.canonical_character_id,
                   annotation.classification,
                   (SELECT COUNT(*) FROM character_panel_growth AS growth
                    WHERE growth.character_id = c.character_id) AS growth_count,
                   (SELECT COUNT(*) FROM character_skill AS skill
                    WHERE skill.character_id = c.character_id) AS skill_count,
                   (SELECT COUNT(*) FROM character_awaken_effect AS awaken
                    WHERE awaken.character_id = c.character_id) AS awakening_count,
                   EXISTS(SELECT 1 FROM character_graduation_template AS graduation
                          WHERE graduation.character_id = c.character_id) AS has_graduation,
                   c.source_row_id,
                   source.row_key AS source_row_key,
                   source.content_sha256 AS source_content_sha256,
                   source.payload_json IS NOT NULL AS source_payload_available,
                   file.source_file_id, file.relative_path AS source_relative_path,
                   file.sha256 AS source_file_sha256
            FROM character AS c
            LEFT JOIN character_annotation AS annotation USING (character_id)
            JOIN source_row AS source ON source.source_row_id = c.source_row_id
            JOIN source_file AS file ON file.source_file_id = source.source_file_id
            WHERE {_CHARACTER_SEARCH_PREDICATE}
            ORDER BY c.character_id
            LIMIT ? OFFSET ?
            """,
            (*_search_parameters(query), max(1, min(int(limit), 200)), max(0, int(offset))),
        )

    def get_catalog_character(self, character_id: int) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT c.character_id, c.name_zh, c.name_text_table, c.name_text_key,
                   c.element_type, c.group_type, c.actor_path, c.mainland_show_time,
                   annotation.logical_character_key,
                   annotation.canonical_character_id,
                   annotation.classification, annotation.annotation_source,
                   (SELECT COUNT(*) FROM character_panel_growth AS growth
                    WHERE growth.character_id = c.character_id) AS growth_count,
                   (SELECT COUNT(*) FROM character_skill AS skill
                    WHERE skill.character_id = c.character_id) AS skill_count,
                   (SELECT COUNT(*) FROM character_awaken_effect AS awaken
                    WHERE awaken.character_id = c.character_id) AS awakening_count,
                   EXISTS(SELECT 1 FROM character_graduation_template AS graduation
                          WHERE graduation.character_id = c.character_id) AS has_graduation,
                   c.source_row_id,
                   source.row_key AS source_row_key,
                   source.content_sha256 AS source_content_sha256,
                   source.payload_json IS NOT NULL AS source_payload_available,
                   file.source_file_id, file.relative_path AS source_relative_path,
                   file.sha256 AS source_file_sha256
            FROM character AS c
            LEFT JOIN character_annotation AS annotation USING (character_id)
            JOIN source_row AS source ON source.source_row_id = c.source_row_id
            JOIN source_file AS file ON file.source_file_id = source.source_file_id
            WHERE c.character_id = ?
            """,
            (int(character_id),),
        )

    def count_catalog_growth_points(self, character_id: int) -> int:
        row = self._one(
            "SELECT COUNT(*) AS count FROM character_panel_growth WHERE character_id = ?",
            (int(character_id),),
        )
        return int((row or {}).get("count", 0))

    def list_catalog_growth_points(
        self,
        character_id: int,
        *,
        limit: int = 40,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT growth.character_id, growth.level, growth.breakthrough_stage,
                   growth.state, growth.hp_base, growth.atk_base, growth.def_base,
                   growth.player_pack_source_row_id,
                   player_source.row_key AS player_pack_source_row_key,
                   player_file.relative_path AS player_pack_source_relative_path,
                   growth.level_modify_source_row_id,
                   level_source.row_key AS level_modify_source_row_key,
                   level_file.relative_path AS level_modify_source_relative_path,
                   growth.breakthrough_modify_source_row_id,
                   breakthrough_source.row_key AS breakthrough_source_row_key,
                   breakthrough_file.relative_path AS breakthrough_source_relative_path
            FROM character_panel_growth AS growth
            JOIN source_row AS player_source
              ON player_source.source_row_id = growth.player_pack_source_row_id
            JOIN source_file AS player_file
              ON player_file.source_file_id = player_source.source_file_id
            JOIN source_row AS level_source
              ON level_source.source_row_id = growth.level_modify_source_row_id
            JOIN source_file AS level_file
              ON level_file.source_file_id = level_source.source_file_id
            LEFT JOIN source_row AS breakthrough_source
              ON breakthrough_source.source_row_id = growth.breakthrough_modify_source_row_id
            LEFT JOIN source_file AS breakthrough_file
              ON breakthrough_file.source_file_id = breakthrough_source.source_file_id
            WHERE growth.character_id = ?
            ORDER BY growth.level, growth.breakthrough_stage
            LIMIT ? OFFSET ?
            """,
            (int(character_id), max(1, min(int(limit), 200)), max(0, int(offset))),
        )

    def list_catalog_breakthrough_points(self, character_id: int) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT growth.character_id, growth.level, growth.breakthrough_stage,
                   growth.state, growth.hp_base, growth.atk_base, growth.def_base,
                   growth.player_pack_source_row_id,
                   player_source.row_key AS player_pack_source_row_key,
                   player_file.relative_path AS player_pack_source_relative_path,
                   growth.level_modify_source_row_id,
                   level_source.row_key AS level_modify_source_row_key,
                   level_file.relative_path AS level_modify_source_relative_path,
                   growth.breakthrough_modify_source_row_id,
                   breakthrough_source.row_key AS breakthrough_source_row_key,
                   breakthrough_file.relative_path AS breakthrough_source_relative_path
            FROM character_panel_growth AS growth
            JOIN source_row AS player_source
              ON player_source.source_row_id = growth.player_pack_source_row_id
            JOIN source_file AS player_file
              ON player_file.source_file_id = player_source.source_file_id
            JOIN source_row AS level_source
              ON level_source.source_row_id = growth.level_modify_source_row_id
            JOIN source_file AS level_file
              ON level_file.source_file_id = level_source.source_file_id
            LEFT JOIN source_row AS breakthrough_source
              ON breakthrough_source.source_row_id = growth.breakthrough_modify_source_row_id
            LEFT JOIN source_file AS breakthrough_file
              ON breakthrough_file.source_file_id = breakthrough_source.source_file_id
            WHERE growth.character_id = ?
              AND growth.state IN ('breakthrough_before', 'breakthrough_after')
            ORDER BY growth.level, growth.breakthrough_stage
            """,
            (int(character_id),),
        )

    def get_catalog_likeability(self, character_id: int) -> dict[str, Any] | None:
        bonus = self._one(
            """
            SELECT bonus.character_id, bonus.required_level, bonus.modify_data_id,
                   bonus.source_row_id, source.row_key AS source_row_key,
                   file.relative_path AS source_relative_path,
                   bonus.modifier_source_row_id,
                   modifier_source.row_key AS modifier_source_row_key,
                   modifier_file.relative_path AS modifier_source_relative_path
            FROM character_likeability_bonus AS bonus
            JOIN source_row AS source ON source.source_row_id = bonus.source_row_id
            JOIN source_file AS file ON file.source_file_id = source.source_file_id
            JOIN source_row AS modifier_source
              ON modifier_source.source_row_id = bonus.modifier_source_row_id
            JOIN source_file AS modifier_file
              ON modifier_file.source_file_id = modifier_source.source_file_id
            WHERE bonus.character_id = ?
            """,
            (int(character_id),),
        )
        if bonus is not None:
            bonus["properties"] = self._rows(
                """
                SELECT property.ordinal, property.property_id, property.value,
                       property.modifier_operation, attribute.display_name_zh,
                       attribute.show_percent, property.source_row_id,
                       source.row_key AS source_row_key,
                       file.relative_path AS source_relative_path
                FROM character_likeability_bonus_property AS property
                LEFT JOIN equipment_attribute AS attribute
                  ON attribute.attribute_id = property.property_id
                JOIN source_row AS source ON source.source_row_id = property.source_row_id
                JOIN source_file AS file ON file.source_file_id = source.source_file_id
                WHERE property.character_id = ?
                ORDER BY property.ordinal
                """,
                (int(character_id),),
            )
        return bonus

    def list_catalog_awakenings(self, character_id: int) -> list[dict[str, Any]]:
        rows = self._rows(
            """
            SELECT awaken.character_id, awaken.effect_id, awaken.ordinal,
                   awaken.awaken_type, awaken.title_zh, awaken.title_text_table,
                   awaken.title_text_key, awaken.description_zh,
                   awaken.description_text_table, awaken.description_text_key,
                   awaken.icon_path, awaken.modify_data_json,
                   awaken.gameplay_effect_ids_json, awaken.source_row_id,
                   source.row_key AS source_row_key,
                   file.relative_path AS source_relative_path
            FROM character_awaken_effect AS awaken
            JOIN source_row AS source ON source.source_row_id = awaken.source_row_id
            JOIN source_file AS file ON file.source_file_id = source.source_file_id
            WHERE awaken.character_id = ?
            ORDER BY awaken.ordinal, awaken.effect_id
            """,
            (int(character_id),),
        )
        bonuses = self._rows(
            """
            SELECT effect_id, ordinal, skill_id, level_delta
            FROM character_awaken_skill_level_bonus
            WHERE character_id = ?
            ORDER BY effect_id, ordinal
            """,
            (int(character_id),),
        )
        by_effect: dict[str, list[dict[str, Any]]] = {}
        for bonus in bonuses:
            by_effect.setdefault(str(bonus["effect_id"]), []).append(bonus)
        for row in rows:
            row["skill_level_bonuses"] = by_effect.get(str(row["effect_id"]), [])
            row["buff_definition_ids"] = tuple(
                str(item["definition_id"])
                for item in self._rows(
                    """
                    SELECT DISTINCT buff.definition_id
                    FROM json_tree(?) AS node
                    JOIN buff_definition AS buff
                      ON node.key = 'AssetPathName'
                     AND (
                         node.atom = buff.asset_path
                         OR node.atom LIKE buff.asset_path || '.%'
                     )
                    ORDER BY buff.definition_id
                    """,
                    (row["modify_data_json"],),
                )
            )
        return rows

    def list_catalog_skills(self, character_id: int) -> list[dict[str, Any]]:
        skills = self._rows(
            """
            SELECT skill.character_id, skill.skill_id, skill.ability_type,
                   skill.ability_index, skill.show_detail_info, skill.gameplay_tag,
                   skill.gameplay_effect_path, skill.reapply_after_revive,
                   ability.name_zh, ability.icon_path, ability.extended_icon_path,
                   ability.gameplay_ability_path,
                   skill.ability_source_row_id,
                   ability_source.row_key AS ability_source_row_key,
                   ability_file.relative_path AS ability_source_relative_path,
                   skill.effect_source_row_id,
                   effect_source.row_key AS effect_source_row_key,
                   effect_file.relative_path AS effect_source_relative_path
            FROM character_skill AS skill
            LEFT JOIN gameplay_ability_catalog AS ability
              ON ability.ability_id = skill.skill_id
            JOIN source_row AS ability_source
              ON ability_source.source_row_id = skill.ability_source_row_id
            JOIN source_file AS ability_file
              ON ability_file.source_file_id = ability_source.source_file_id
            LEFT JOIN source_row AS effect_source
              ON effect_source.source_row_id = skill.effect_source_row_id
            LEFT JOIN source_file AS effect_file
              ON effect_file.source_file_id = effect_source.source_file_id
            WHERE skill.character_id = ?
            ORDER BY skill.ability_index, skill.skill_id
            """,
            (int(character_id),),
        )
        for skill in skills:
            skill_id = str(skill["skill_id"])
            skill["levels"] = self._rows(
                """
                SELECT level, required_breakthrough_stage,
                       required_awaken_level, cost_items_json
                FROM character_skill_level
                WHERE character_id = ? AND skill_id = ?
                ORDER BY level
                """,
                (int(character_id), skill_id),
            )
            skill["descriptions"] = self._rows(
                """
                SELECT ordinal, description_type, title_zh, description_zh,
                       short_description_zh, unlock_id, unlock_description_zh,
                       replacement_values_json
                FROM gameplay_ability_description
                WHERE ability_id = ?
                ORDER BY ordinal
                """,
                (skill_id,),
            )
            skill["level_hints"] = self._rows(
                """
                SELECT ordinal, name_id, description_zh, value_description_zh,
                       global_curve_id, source_type, damage_effect_ids_json,
                       defense_effect_ids_json, health_effect_ids_json
                FROM gameplay_ability_level_hint
                WHERE ability_id = ?
                ORDER BY ordinal
                """,
                (skill_id,),
            )
            skill["damage_items"] = self._rows(
                """
                SELECT damage_id, damage_type
                FROM skill_damage
                WHERE ability_id = ?
                ORDER BY damage_id
                """,
                (skill_id,),
            )
        return skills

    def get_catalog_equipment_plan(
        self, character_id: int,
    ) -> dict[str, Any] | None:
        plan = self._one(
            """
            SELECT character_id, core_item_id, core_level, module_level
            FROM equipment_plan WHERE character_id = ?
            """,
            (int(character_id),),
        )
        if plan is None:
            return None
        plan["modules"] = self._rows(
            """
            SELECT module.ordinal, module.item_id, item.name_zh,
                   item.geometry_id, item.grid_count
            FROM equipment_plan_module AS module
            LEFT JOIN equipment_item AS item ON item.item_id = module.item_id
            WHERE module.character_id = ? ORDER BY module.ordinal
            """,
            (int(character_id),),
        )
        for module in plan["modules"]:
            module["shape_cells"] = self._rows(
                """SELECT x, y FROM equipment_shape_cell
                   WHERE shape_id = ? ORDER BY ordinal""",
                (module.get("geometry_id"),),
            )
        plan["anchors"] = self._rows(
            """
            SELECT row, column, anchor_item_id
            FROM equipment_plan_cell
            WHERE character_id = ? AND anchor_item_id IS NOT NULL
            ORDER BY anchor_item_id, row, column
            """,
            (int(character_id),),
        )
        plan["core_attributes"] = self._rows(
            """
            SELECT value.attribute_id AS property_id, attribute.display_name_zh,
                   attribute.show_percent
            FROM equipment_plan_core_attribute AS value
            LEFT JOIN equipment_attribute AS attribute
              ON attribute.attribute_id = value.attribute_id
            WHERE value.character_id = ? ORDER BY value.ordinal
            """,
            (int(character_id),),
        )
        plan["recommended_attributes"] = self._rows(
            """
            SELECT value.attribute_id AS property_id, attribute.display_name_zh,
                   attribute.show_percent
            FROM equipment_plan_recommended_attribute AS value
            LEFT JOIN equipment_attribute AS attribute
              ON attribute.attribute_id = value.attribute_id
            WHERE value.character_id = ? ORDER BY value.ordinal
            """,
            (int(character_id),),
        )
        return plan

    def get_catalog_character_shape_bonus(
        self, character_id: int,
    ) -> dict[str, Any] | None:
        bonus = self._one(
            """
            SELECT value.logical_character_key, value.shape_label,
                   value.shape_grid_count
            FROM character_annotation AS annotation
            JOIN logical_character_shape_bonus AS value
              ON value.logical_character_key = annotation.logical_character_key
            WHERE annotation.character_id = ?
            """,
            (int(character_id),),
        )
        if bonus is None:
            return None
        bonus["properties"] = self._rows(
            """
            SELECT value.property_id, value.display_value,
                   attribute.display_name_zh, attribute.show_percent
            FROM logical_character_shape_bonus_property AS value
            LEFT JOIN equipment_attribute AS attribute
              ON attribute.attribute_id = value.property_id
            WHERE value.logical_character_key = ? ORDER BY value.ordinal
            """,
            (bonus["logical_character_key"],),
        )
        return bonus

    def get_catalog_character_weights(
        self, character_id: int,
    ) -> dict[str, Any] | None:
        weights = self._one(
            """SELECT character_id FROM character_weight_recommendation
               WHERE character_id = ?""",
            (int(character_id),),
        )
        if weights is None:
            return None
        weights["properties"] = self._rows(
            """
            SELECT value.property_id, value.weight, value.main_weight,
                   attribute.display_name_zh, attribute.show_percent
            FROM character_weight_recommendation_property AS value
            LEFT JOIN equipment_attribute AS attribute
              ON attribute.attribute_id = value.property_id
            WHERE value.character_id = ? ORDER BY value.ordinal
            """,
            (int(character_id),),
        )
        return weights

    def get_catalog_cultivation(self, character_id: int) -> dict[str, Any] | None:
        guide = self._one(
            """
            SELECT guide.character_id, guide.display_text, guide.s_score,
                   guide.a_score, guide.icon_path,
                   guide.recommend_attribute_jump_id, guide.role_sex_change,
                   guide.source_row_id, source.row_key AS source_row_key,
                   file.relative_path AS source_relative_path
            FROM character_cultivation_guide AS guide
            JOIN source_row AS source ON source.source_row_id = guide.source_row_id
            JOIN source_file AS file ON file.source_file_id = source.source_file_id
            WHERE guide.character_id = ?
            """,
            (int(character_id),),
        )
        if guide is None:
            return None
        guide["fork_recommendations"] = self._rows(
            """
            SELECT recommendation.ordinal, recommendation.fork_id,
                   fork.name_zh AS fork_name_zh, recommendation.description_zh,
                   recommendation.source_kind, fork.icon_path
            FROM character_cultivation_fork_recommendation AS recommendation
            JOIN fork_item AS fork ON fork.fork_id = recommendation.fork_id
            WHERE recommendation.character_id = ?
            ORDER BY recommendation.ordinal
            """,
            (int(character_id),),
        )
        guide["attribute_recommendations"] = self._rows(
            """
            SELECT recommendation.ordinal, recommendation.property_id,
                   attribute.display_name_zh
            FROM character_cultivation_attribute_recommendation AS recommendation
            LEFT JOIN equipment_attribute AS attribute
              ON attribute.attribute_id = recommendation.property_id
            WHERE recommendation.character_id = ?
            ORDER BY recommendation.ordinal
            """,
            (int(character_id),),
        )
        guide["stages"] = self._rows(
            """
            SELECT stage_ordinal, character_level, fork_level, core_item_id,
                   core_level, equipment_level
            FROM character_cultivation_stage
            WHERE character_id = ?
            ORDER BY stage_ordinal
            """,
            (int(character_id),),
        )
        guide["stage_skills"] = self._rows(
            """
            SELECT stage_ordinal, sex_kind, ordinal, ability_id, recommended_level
            FROM character_cultivation_stage_skill
            WHERE character_id = ?
            ORDER BY stage_ordinal, sex_kind, ordinal
            """,
            (int(character_id),),
        )
        return guide

    def get_catalog_graduation(self, character_id: int) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT graduation.character_id, graduation.source_kind,
                   graduation.fork_id, fork.name_zh AS fork_name_zh,
                   graduation.fork_level, graduation.fork_refinement_level,
                   graduation.core_suit_id, suit.name_zh AS core_suit_name_zh,
                   graduation.core_main_property_id,
                   attribute.display_name_zh AS core_main_property_name_zh,
                   graduation.drive_area, graduation.extra_shape_count,
                   graduation.benchmark_damage, graduation.profile_json,
                   graduation.equipment_json, graduation.generated_at_utc,
                   fork.icon_path AS fork_icon_path,
                   fork.card_path AS fork_card_path,
                   fork.painting_path AS fork_painting_path,
                   fork.source_row_id AS fork_source_row_id,
                   fork_source.row_key AS fork_source_row_key,
                   fork_file.relative_path AS fork_source_relative_path
            FROM character_graduation_template AS graduation
            LEFT JOIN fork_item AS fork ON fork.fork_id = graduation.fork_id
            LEFT JOIN source_row AS fork_source
              ON fork_source.source_row_id = fork.source_row_id
            LEFT JOIN source_file AS fork_file
              ON fork_file.source_file_id = fork_source.source_file_id
            LEFT JOIN equipment_suit AS suit
              ON suit.suit_id = graduation.core_suit_id
            LEFT JOIN equipment_attribute AS attribute
              ON attribute.attribute_id = graduation.core_main_property_id
            WHERE graduation.character_id = ?
            """,
            (int(character_id),),
        )

    def count_catalog_combat_links(self, character_id: int) -> int:
        row = self._one(
            """
            SELECT (
                SELECT COUNT(*)
                FROM character_combat_ability_binding AS binding
                LEFT JOIN combat_ability_effect_binding AS effect
                  ON effect.ability_asset_path = binding.ability_asset_path
                WHERE binding.character_id = ?
            ) + (
                SELECT COUNT(*) FROM buff_definition AS buff
                WHERE buff.owner_character_id = ?
            ) AS count
            """,
            (int(character_id), int(character_id)),
        )
        return int((row or {}).get("count", 0))

    def list_catalog_combat_links(
        self,
        character_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._rows(
            """
            WITH links AS (
                SELECT 0 AS sort_group, binding.binding_kind,
                       binding.ordinal AS binding_ordinal, binding.input_id,
                       binding.ability_id, binding.ability_asset_path,
                       effect.event_tag, effect.ordinal AS effect_ordinal,
                       effect.effect_id, effect.effect_asset_path,
                       effect.target_type_asset_path,
                       catalog.gameplay_effect_index,
                       catalog.class_path AS gameplay_effect_class_path,
                       buff.definition_id AS buff_definition_id,
                       buff.definition_kind AS buff_definition_kind,
                       buff.duration_policy, buff.stacking_type,
                       buff.stack_limit_count,
                       COALESCE(buff_file.relative_path, effect_file.relative_path)
                           AS source_relative_path
                FROM character_combat_ability_binding AS binding
                LEFT JOIN combat_ability_effect_binding AS effect
                  ON effect.ability_asset_path = binding.ability_asset_path
                LEFT JOIN gameplay_effect_catalog AS catalog
                  ON catalog.gameplay_effect_id = effect.effect_id
                LEFT JOIN buff_definition AS buff
                  ON buff.asset_path = effect.effect_asset_path
                LEFT JOIN source_file AS buff_file
                  ON buff_file.source_file_id = buff.source_file_id
                LEFT JOIN combat_blueprint_asset AS effect_asset
                  ON effect_asset.asset_path = effect.effect_asset_path
                LEFT JOIN source_file AS effect_file
                  ON effect_file.source_file_id = effect_asset.source_file_id
                WHERE binding.character_id = ?

                UNION ALL

                SELECT 1 AS sort_group, 'owned_buff' AS binding_kind,
                       0 AS binding_ordinal, NULL AS input_id,
                       NULL AS ability_id, NULL AS ability_asset_path,
                       NULL AS event_tag, 0 AS effect_ordinal,
                       buff.definition_id AS effect_id,
                       buff.asset_path AS effect_asset_path,
                       NULL AS target_type_asset_path,
                       catalog.gameplay_effect_index,
                       catalog.class_path AS gameplay_effect_class_path,
                       buff.definition_id AS buff_definition_id,
                       buff.definition_kind AS buff_definition_kind,
                       buff.duration_policy, buff.stacking_type,
                       buff.stack_limit_count,
                       file.relative_path AS source_relative_path
                FROM buff_definition AS buff
                LEFT JOIN gameplay_effect_catalog AS catalog
                  ON catalog.gameplay_effect_id = buff.definition_id
                JOIN source_file AS file ON file.source_file_id = buff.source_file_id
                WHERE buff.owner_character_id = ?
            )
            SELECT * FROM links
            ORDER BY sort_group, binding_kind, binding_ordinal,
                     COALESCE(event_tag, ''), effect_ordinal,
                     COALESCE(effect_asset_path, '')
            LIMIT ? OFFSET ?
            """,
            (
                int(character_id),
                int(character_id),
                max(1, min(int(limit), 500)),
                max(0, int(offset)),
            ),
        )
