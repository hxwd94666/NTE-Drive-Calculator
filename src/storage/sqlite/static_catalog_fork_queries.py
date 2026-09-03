# 提供游戏资料库弧盘域的只读查询。
"""Focused read-only queries for the static-catalog fork domain."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .static_game_data_dao import StaticGameDataDao


def _like_pattern(value: str) -> str:
    """Escape user text for a literal, case-insensitive ``LIKE`` search."""

    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class StaticCatalogForkDao(StaticGameDataDao):
    """Narrow DAO used by the read-only fork catalog.

    The base DAO owns the schema-v29 check and opens SQLite with ``mode=ro``.
    This subclass only adds fixed, parameterized domain queries; callers cannot
    supply table names, selected columns, ordering expressions, or raw SQL.
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        super().__init__(database_path)

    def fork_catalog_metadata(self) -> dict[str, Any]:
        dataset = self._one(
            """
            SELECT dataset_id, importer_version, built_at_utc,
                   (SELECT MAX(version) FROM schema_migration) AS schema_version
            FROM dataset
            """
        ) or {}
        counts = {
            str(row["table_name"]): int(row["row_count"])
            for row in self._rows(
                """
                SELECT 'fork_item' AS table_name, COUNT(*) AS row_count FROM fork_item
                UNION ALL SELECT 'fork_upgrade_level', COUNT(*) FROM fork_upgrade_level
                UNION ALL SELECT 'fork_breakthrough', COUNT(*) FROM fork_breakthrough
                UNION ALL SELECT 'fork_modify_value', COUNT(*) FROM fork_modify_value
                UNION ALL SELECT 'fork_star_level', COUNT(*) FROM fork_star_level
                UNION ALL SELECT 'fork_star_parameter', COUNT(*) FROM fork_star_parameter
                UNION ALL SELECT 'fork_refinement_parameter_value', COUNT(*)
                  FROM fork_refinement_parameter_value
                UNION ALL SELECT 'character_cultivation_fork_recommendation', COUNT(*)
                  FROM character_cultivation_fork_recommendation
                """
            )
        }
        capabilities = self._one(
            """
            SELECT
              EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table'
                     AND name = 'fork_skill') AS has_fork_skill,
              EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table'
                     AND name = 'fork_skill_level') AS has_fork_skill_level,
              (SELECT COUNT(*) FROM source_row WHERE payload_json IS NOT NULL)
                AS preserved_source_payloads,
              (SELECT COUNT(*) FROM combat_effect_definition
                 WHERE effect_definition_id LIKE 'fork_star:%')
                AS projected_effect_definitions,
              (SELECT COUNT(*) FROM combat_effect_buff_link
                 WHERE effect_definition_id LIKE 'fork_star:%')
                AS projected_buff_links
            """
        ) or {}
        return {"dataset": dataset, "counts": counts, "capabilities": capabilities}

    def list_fork_catalog_types(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT t.fork_type_id, t.name_zh, t.description_zh, t.icon_path,
                   COUNT(f.fork_id) AS fork_count
            FROM fork_type AS t
            LEFT JOIN fork_item AS f USING (fork_type_id)
            GROUP BY t.fork_type_id, t.name_zh, t.description_zh, t.icon_path
            ORDER BY t.fork_type_id
            """
        )

    @staticmethod
    def _fork_filter(
        *,
        query: str,
        quality: str | None,
        fork_type_id: int | None,
        character_id: int | None,
    ) -> tuple[str, tuple[Any, ...]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if query.strip():
            pattern = _like_pattern(query.strip())
            clauses.append(
                """
                (
                    f.fork_id LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR f.name_zh LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR COALESCE(t.name_zh, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR COALESCE(f.raw_group_type, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR COALESCE(f.upgrade_pack_id, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR COALESCE(f.breakthrough_pack_id, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR COALESCE(f.star_pack_id, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR COALESCE(f.icon_path, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR COALESCE(f.card_path, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR COALESCE(f.painting_path, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
                    OR EXISTS (
                        SELECT 1
                        FROM json_each(f.exclusive_character_ids_json) AS ids
                        LEFT JOIN character AS c
                          ON c.character_id = CAST(ids.value AS INTEGER)
                        WHERE CAST(ids.value AS TEXT) LIKE ? ESCAPE '\\' COLLATE NOCASE
                           OR COALESCE(c.name_zh, '') LIKE ? ESCAPE '\\' COLLATE NOCASE
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM character_cultivation_fork_recommendation AS r
                        JOIN character AS c USING (character_id)
                        WHERE r.fork_id = f.fork_id
                          AND (CAST(r.character_id AS TEXT) LIKE ? ESCAPE '\\'
                               OR c.name_zh LIKE ? ESCAPE '\\' COLLATE NOCASE)
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM fork_star_parameter AS p
                        WHERE lower(p.star_pack_id) = lower(f.star_pack_id)
                          AND p.name_id LIKE ? ESCAPE '\\' COLLATE NOCASE
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM combat_effect_buff_link AS l
                        WHERE l.effect_definition_id LIKE
                              'fork_star:' || f.star_pack_id || ':%'
                          AND l.target_asset_path LIKE ? ESCAPE '\\' COLLATE NOCASE
                    )
                )
                """
            )
            parameters.extend((pattern,) * 16)
        if quality:
            clauses.append("f.quality = ? COLLATE NOCASE")
            parameters.append(str(quality))
        if fork_type_id is not None:
            clauses.append("f.fork_type_id = ?")
            parameters.append(int(fork_type_id))
        if character_id is not None:
            clauses.append(
                """
                (
                    EXISTS (
                        SELECT 1 FROM json_each(f.exclusive_character_ids_json) AS ids
                        WHERE CAST(ids.value AS INTEGER) = ?
                    )
                    OR EXISTS (
                        SELECT 1 FROM character_cultivation_fork_recommendation AS r
                        WHERE r.fork_id = f.fork_id AND r.character_id = ?
                    )
                )
                """
            )
            parameters.extend((int(character_id), int(character_id)))
        return (" AND ".join(clauses) if clauses else "1 = 1", tuple(parameters))

    def count_fork_catalog_items(
        self,
        *,
        query: str = "",
        quality: str | None = None,
        fork_type_id: int | None = None,
        character_id: int | None = None,
    ) -> int:
        where, parameters = self._fork_filter(
            query=query,
            quality=quality,
            fork_type_id=fork_type_id,
            character_id=character_id,
        )
        row = self._one(
            f"""
            SELECT COUNT(*) AS item_count
            FROM fork_item AS f
            LEFT JOIN fork_type AS t USING (fork_type_id)
            WHERE {where}
            """,
            parameters,
        )
        return int((row or {}).get("item_count", 0))

    def list_fork_catalog_items(
        self,
        *,
        query: str = "",
        quality: str | None = None,
        fork_type_id: int | None = None,
        character_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where, parameters = self._fork_filter(
            query=query,
            quality=quality,
            fork_type_id=fork_type_id,
            character_id=character_id,
        )
        return self._rows(
            f"""
            SELECT f.fork_id, f.name_zh, f.description_zh, f.quality,
                   f.fork_type_id, t.name_zh AS fork_type_name_zh,
                   f.raw_group_type, f.max_breakthrough, f.max_star,
                   f.icon_path, f.card_path, f.painting_path,
                   json_array_length(f.exclusive_character_ids_json)
                     AS exclusive_character_count,
                   (SELECT COUNT(*)
                      FROM character_cultivation_fork_recommendation AS r
                     WHERE r.fork_id = f.fork_id) AS recommendation_count
            FROM fork_item AS f
            LEFT JOIN fork_type AS t USING (fork_type_id)
            WHERE {where}
            ORDER BY f.quality, f.fork_type_id, f.name_zh, f.fork_id
            LIMIT ? OFFSET ?
            """,
            (*parameters, max(1, min(200, int(limit))), max(0, int(offset))),
        )

    def get_fork_catalog_item(self, fork_id: str) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT f.*, t.name_zh AS fork_type_name_zh,
                   t.description_zh AS fork_type_description_zh,
                   t.icon_path AS fork_type_icon_path,
                   sr.row_key, sr.content_sha256,
                   sr.payload_json IS NOT NULL AS payload_preserved,
                   sf.relative_path, sf.sha256 AS source_file_sha256
            FROM fork_item AS f
            LEFT JOIN fork_type AS t USING (fork_type_id)
            JOIN source_row AS sr ON sr.source_row_id = f.source_row_id
            JOIN source_file AS sf ON sf.source_file_id = sr.source_file_id
            WHERE f.fork_id = ?
            """,
            (str(fork_id),),
        )

    def list_fork_character_relations(self, fork_id: str) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT 'exclusive_character' AS relation_kind,
                   CAST(ids.value AS INTEGER) AS character_id,
                   c.name_zh, NULL AS ordinal,
                   'exclusive_character_ids_json' AS source_kind,
                   f.source_row_id, sr.row_key, sr.content_sha256,
                   sf.relative_path, sf.sha256 AS source_file_sha256
            FROM fork_item AS f
            JOIN json_each(f.exclusive_character_ids_json) AS ids
            LEFT JOIN character AS c
              ON c.character_id = CAST(ids.value AS INTEGER)
            JOIN source_row AS sr ON sr.source_row_id = f.source_row_id
            JOIN source_file AS sf ON sf.source_file_id = sr.source_file_id
            WHERE f.fork_id = ?
            UNION ALL
            SELECT 'cultivation_recommendation', r.character_id, c.name_zh,
                   r.ordinal, r.source_kind, g.source_row_id, sr.row_key,
                   sr.content_sha256, sf.relative_path,
                   sf.sha256 AS source_file_sha256
            FROM character_cultivation_fork_recommendation AS r
            JOIN character AS c USING (character_id)
            JOIN character_cultivation_guide AS g USING (character_id)
            JOIN source_row AS sr ON sr.source_row_id = g.source_row_id
            JOIN source_file AS sf USING (source_file_id)
            WHERE r.fork_id = ?
            ORDER BY relation_kind, ordinal, character_id
            """,
            (str(fork_id), str(fork_id)),
        )

    def list_fork_growth_rows(self, fork_id: str) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT u.level, u.need_exp, u.modify_pack_id,
                   mp.conditions_json, mv.ordinal AS modifier_ordinal,
                   mv.property_id, a.display_name_zh AS property_name_zh,
                   a.show_percent, mv.value, mv.operation, mv.sort_key,
                   u.source_row_id, usr.row_key, usr.content_sha256,
                   usf.relative_path, usf.sha256 AS source_file_sha256,
                   mp.source_row_id AS modify_source_row_id,
                   msr.row_key AS modify_row_key,
                   msr.content_sha256 AS modify_content_sha256,
                   msf.relative_path AS modify_relative_path,
                   msf.sha256 AS modify_source_file_sha256
            FROM fork_item AS f
            JOIN fork_upgrade_level AS u
              ON lower(u.upgrade_pack_id) = lower(f.upgrade_pack_id)
            LEFT JOIN fork_modify_pack AS mp
              ON lower(mp.modify_pack_id) = lower(u.modify_pack_id)
            LEFT JOIN fork_modify_value AS mv
              ON mv.modify_pack_id = mp.modify_pack_id
            LEFT JOIN equipment_attribute AS a
              ON a.attribute_id = mv.property_id
            JOIN source_row AS usr ON usr.source_row_id = u.source_row_id
            JOIN source_file AS usf ON usf.source_file_id = usr.source_file_id
            LEFT JOIN source_row AS msr ON msr.source_row_id = mp.source_row_id
            LEFT JOIN source_file AS msf ON msf.source_file_id = msr.source_file_id
            WHERE f.fork_id = ?
            ORDER BY u.level, mv.ordinal
            """,
            (str(fork_id),),
        )

    def list_fork_breakthrough_rows(self, fork_id: str) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT b.stage, b.max_fork_level, b.need_items, b.need_gold,
                   b.modify_pack_id, mp.conditions_json,
                   mv.ordinal AS modifier_ordinal, mv.property_id,
                   a.display_name_zh AS property_name_zh, a.show_percent,
                   mv.value, mv.operation, mv.sort_key,
                   b.source_row_id, sr.row_key, sr.content_sha256,
                   sf.relative_path, sf.sha256 AS source_file_sha256
            FROM fork_item AS f
            JOIN fork_breakthrough AS b
              ON lower(b.breakthrough_pack_id) = lower(f.breakthrough_pack_id)
            LEFT JOIN fork_modify_pack AS mp
              ON lower(mp.modify_pack_id) = lower(b.modify_pack_id)
            LEFT JOIN fork_modify_value AS mv
              ON mv.modify_pack_id = mp.modify_pack_id
            LEFT JOIN equipment_attribute AS a
              ON a.attribute_id = mv.property_id
            JOIN source_row AS sr ON sr.source_row_id = b.source_row_id
            JOIN source_file AS sf ON sf.source_file_id = sr.source_file_id
            WHERE f.fork_id = ?
            ORDER BY b.stage, mv.ordinal
            """,
            (str(fork_id),),
        )

    def list_fork_refinement_rows(self, fork_id: str) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT s.star_level, s.title_zh, s.description_zh, s.need_gold,
                   s.buffs_json, s.source_row_id, sr.row_key, sr.content_sha256,
                   sf.relative_path, sf.sha256 AS source_file_sha256,
                   d.effect_definition_id, d.effect_kind, d.activation_kind,
                   d.parameters_json AS projected_parameters_json,
                   d.formula_version
            FROM fork_item AS f
            JOIN fork_star_level AS s
              ON lower(s.star_pack_id) = lower(f.star_pack_id)
            JOIN source_row AS sr ON sr.source_row_id = s.source_row_id
            JOIN source_file AS sf ON sf.source_file_id = sr.source_file_id
            LEFT JOIN combat_effect_definition AS d
              ON d.effect_definition_id =
                 'fork_star:' || s.star_pack_id || ':' || s.star_level
            WHERE f.fork_id = ?
            ORDER BY s.star_level
            """,
            (str(fork_id),),
        )

    def list_fork_refinement_parameters(self, fork_id: str) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT p.star_level, p.ordinal, p.name_id, p.is_percent, v.value,
                   v.source_row_id, sr.row_key, sr.content_sha256,
                   sf.relative_path, sf.sha256 AS source_file_sha256
            FROM fork_item AS f
            JOIN fork_star_parameter AS p
              ON lower(p.star_pack_id) = lower(f.star_pack_id)
            LEFT JOIN fork_refinement_parameter_value AS v
              ON v.name_id = p.name_id AND v.refinement_level = p.star_level
            LEFT JOIN source_row AS sr ON sr.source_row_id = v.source_row_id
            LEFT JOIN source_file AS sf ON sf.source_file_id = sr.source_file_id
            WHERE f.fork_id = ?
            ORDER BY p.star_level, p.ordinal
            """,
            (str(fork_id),),
        )

    def list_fork_buff_links(self, fork_id: str) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT s.star_level, l.ordinal, l.link_kind, l.target_asset_path,
                   l.target_available, b.definition_id, b.definition_kind,
                   b.owner_character_id, b.duration_policy,
                   b.duration_magnitude_json, b.period_json, b.stacking_type,
                   b.stack_limit_count, sf.relative_path,
                   sf.sha256 AS source_file_sha256,
                   ge.gameplay_effect_index, ge.gameplay_effect_id,
                   ge.class_path AS gameplay_effect_class_path
            FROM fork_item AS f
            JOIN fork_star_level AS s
              ON lower(s.star_pack_id) = lower(f.star_pack_id)
            JOIN combat_effect_buff_link AS l
              ON l.effect_definition_id =
                 'fork_star:' || s.star_pack_id || ':' || s.star_level
            LEFT JOIN buff_definition AS b
              ON lower(b.asset_path) = lower(l.target_asset_path)
            LEFT JOIN source_file AS sf USING (source_file_id)
            LEFT JOIN gameplay_effect_catalog AS ge
              ON lower(ge.class_path) LIKE lower(l.target_asset_path) || '.%'
            WHERE f.fork_id = ?
            ORDER BY s.star_level, l.ordinal, ge.gameplay_effect_index
            """,
            (str(fork_id),),
        )

    def list_fork_buff_modifiers(self, fork_id: str) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT s.star_level, l.ordinal AS link_ordinal, m.*,
                   a.display_name_zh AS property_name_zh,
                   a.show_percent AS property_show_percent
            FROM fork_item AS f
            JOIN fork_star_level AS s
              ON lower(s.star_pack_id) = lower(f.star_pack_id)
            JOIN combat_effect_buff_link AS l
              ON l.effect_definition_id =
                 'fork_star:' || s.star_pack_id || ':' || s.star_level
            JOIN buff_modifier AS m
              ON lower(m.asset_path) = lower(l.target_asset_path)
            LEFT JOIN equipment_attribute AS a
              ON a.attribute_id = m.property_id
            WHERE f.fork_id = ?
            ORDER BY s.star_level, l.ordinal, m.ordinal
            """,
            (str(fork_id),),
        )

    def list_fork_buff_triggers(self, fork_id: str) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT s.star_level, l.ordinal AS link_ordinal,
                   t.asset_path, t.ordinal, t.event_type, t.effect_type,
                   t.target_effect_asset_path, t.stack_count, t.by_self,
                   t.target_trigger, t.modify_duration_json,
                   t.application_requirement_asset_path,
                   target.definition_id AS target_definition_id,
                   ge.gameplay_effect_index AS target_gameplay_effect_index,
                   ge.gameplay_effect_id AS target_gameplay_effect_id,
                   ge.class_path AS target_gameplay_effect_class_path
            FROM fork_item AS f
            JOIN fork_star_level AS s
              ON lower(s.star_pack_id) = lower(f.star_pack_id)
            JOIN combat_effect_buff_link AS l
              ON l.effect_definition_id =
                 'fork_star:' || s.star_pack_id || ':' || s.star_level
            JOIN buff_trigger_effect AS t
              ON lower(t.asset_path) = lower(l.target_asset_path)
            LEFT JOIN buff_definition AS target
              ON lower(target.asset_path) = lower(t.target_effect_asset_path)
            LEFT JOIN gameplay_effect_catalog AS ge
              ON lower(ge.class_path) LIKE
                 lower(t.target_effect_asset_path) || '.%'
            WHERE f.fork_id = ?
            ORDER BY s.star_level, l.ordinal, t.ordinal,
                     ge.gameplay_effect_index
            """,
            (str(fork_id),),
        )

    def list_fork_gameplay_abilities(self, fork_id: str) -> list[dict[str, Any]]:
        """Return only exact asset-path GA relations; schema v30 normally has none."""

        return self._rows(
            """
            WITH root_paths(path) AS (
                SELECT DISTINCT l.target_asset_path
                FROM fork_item AS f
                JOIN fork_star_level AS s
                  ON lower(s.star_pack_id) = lower(f.star_pack_id)
                JOIN combat_effect_buff_link AS l
                  ON l.effect_definition_id =
                     'fork_star:' || s.star_pack_id || ':' || s.star_level
                WHERE f.fork_id = ?
            ), related_paths(path) AS (
                SELECT path FROM root_paths
                UNION
                SELECT t.target_effect_asset_path
                FROM buff_trigger_effect AS t
                JOIN root_paths AS r ON lower(r.path) = lower(t.asset_path)
            )
            SELECT DISTINCT ga.ability_id, ga.name_zh, ga.gameplay_ability_path
            FROM gameplay_ability_catalog AS ga
            JOIN related_paths AS p
              ON lower(ga.gameplay_ability_path) = lower(p.path)
              OR lower(ga.gameplay_ability_path) LIKE lower(p.path) || '.%'
            ORDER BY ga.ability_id
            """,
            (str(fork_id),),
        )
