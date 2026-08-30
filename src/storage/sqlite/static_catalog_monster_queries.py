# 游戏资料库怪物与玩法域的窄只读查询。
"""Narrow read-only queries for the monster and encounter catalog domain."""

from __future__ import annotations

from typing import Any

from src.storage.sqlite.static_catalog_feast_queries import (
    StaticCatalogHistoricalFeastQueriesMixin,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


_INDEX_CTE = """
WITH rotation_state AS (
    SELECT r.*,
           CASE
             WHEN r.starts_at_mainland <= :as_of
              AND r.ends_at_mainland >= :as_of THEN 'current'
             WHEN r.starts_at_mainland = (
                 SELECT MIN(n.starts_at_mainland)
                 FROM outer_realm_rotation AS n
                 WHERE n.starts_at_mainland > :as_of
             ) THEN 'next'
             WHEN r.ends_at_mainland < :as_of THEN 'historical'
             ELSE 'scheduled'
           END AS release_state
    FROM outer_realm_rotation AS r
),
catalog_entry AS (
    SELECT 'manual_monster' AS entity_kind,
           m.monster_manual_id AS identity_1, '' AS identity_2,
           '' AS identity_3, 'monster' AS domain,
           'official_illustrated' AS play_mode,
           COALESCE(m.place_zh, '') AS region,
           '' AS difficulty, d.dataset_id AS version_id,
           '' AS release_state, m.name_zh AS title_zh,
           m.monster_manual_id AS primary_id, '' AS secondary_id,
           COALESCE(m.image_path, '') AS resource_path,
           m.source_row_id,
           printf('01:%08d:%s', m.sort_order, m.monster_manual_id) AS sort_key,
           lower(COALESCE(m.name_zh, '') || ' ' || m.monster_manual_id || ' ' ||
                 COALESCE(m.enemy_type, '') || ' ' || COALESCE(m.place_zh, '') || ' ' ||
                 COALESCE((SELECT group_concat(a.alias_value, ' ')
                           FROM monster_identifier_alias AS a
                           WHERE a.monster_manual_id = m.monster_manual_id), '')) AS search_text
    FROM monster_catalog AS m CROSS JOIN dataset AS d
    UNION ALL
    SELECT 'profile_monster', p.static_table, p.monster_id, '', 'monster',
           'template_profile', p.static_table, CAST(p.monster_level AS TEXT),
           d.dataset_id, '',
           (SELECT MIN(m.name_zh)
              FROM monster_template_binding AS b
              JOIN monster_catalog AS m USING (monster_manual_id)
             WHERE lower(b.monster_template_name) = lower(p.monster_id)),
           p.monster_id, p.static_table, '', p.source_row_id,
           '02:' || lower(p.static_table) || ':' || lower(p.monster_id),
           lower(p.static_table || ' ' || p.monster_id || ' ' ||
                 COALESCE(p.default_profile_set, '') || ' ' ||
                 COALESCE(p.default_pack_id, '') || ' ' ||
                 COALESCE((SELECT group_concat(b.monster_manual_id, ' ')
                           FROM monster_template_binding AS b
                           WHERE lower(b.monster_template_name) = lower(p.monster_id)), ''))
    FROM monster_instance_profile AS p CROSS JOIN dataset AS d
    UNION ALL
    SELECT 'world_boss', m.monster_manual_id, '', '', 'encounter',
           'world_boss', COALESCE(m.place_zh, ''), '', d.dataset_id, '',
           m.name_zh, m.monster_manual_id, 'WeeklyBoss',
           COALESCE(m.world_image_path, m.image_path, ''), m.source_row_id,
           '10:' || printf('%08d', m.sort_order) || ':' || m.monster_manual_id,
           lower(COALESCE(m.name_zh, '') || ' ' || m.monster_manual_id || ' WeeklyBoss')
    FROM monster_catalog AS m CROSS JOIN dataset AS d
    WHERE m.enemy_type = 'WeeklyBoss'
    UNION ALL
    SELECT 'feast', s.stage_id, CAST(fd.difficulty_id AS TEXT), '', 'encounter',
           'feast', '争锋赏宴', CAST(fd.difficulty_id AS TEXT),
           d.dataset_id, '', s.name_zh, s.stage_id, s.boss_monster_id,
           COALESCE(fd.boss_icon_path, ''), s.source_row_id,
           '20:' || s.stage_id || ':' || printf('%04d', fd.difficulty_id),
           lower(COALESCE(s.name_zh, '') || ' ' || COALESCE(fd.boss_name_zh, '') || ' ' ||
                 s.stage_id || ' ' || s.boss_monster_id || ' ' || fd.profile_set || ' ' || fd.pack_id)
    FROM feast_stage AS s
    JOIN feast_stage_difficulty AS fd USING (stage_id)
    CROSS JOIN dataset AS d
    UNION ALL
    SELECT 'outer_realm', l.level_config_id, CAST(l.level_id AS TEXT),
           sp.fight_stage, 'encounter', 'outer_realm', '轨外之境',
           CAST(l.level_id AS TEXT), l.level_config_id,
           COALESCE(rs.release_state, 'unscheduled'),
           l.name_zh, l.level_config_id, sp.fight_stage, '', l.source_row_id,
           '30:' || printf('%02d', COALESCE(rs.inference_ordinal, 99)) || ':' ||
             l.level_config_id || ':' || printf('%04d', l.level_id) || ':' || sp.fight_stage,
           lower(COALESCE(l.name_zh, '') || ' ' || l.level_config_id || ' ' ||
                 sp.fight_stage || ' ' || COALESCE((
                    SELECT group_concat(pe.monster_class_path, ' ')
                    FROM abyss_level_monster_spawn AS sx
                    JOIN abyss_monster_pool_entry AS pe USING (monster_pool_id)
                    WHERE sx.level_config_id = l.level_config_id
                      AND sx.level_id = l.level_id
                      AND sx.fight_stage = sp.fight_stage), ''))
    FROM abyss_level AS l
    JOIN (SELECT DISTINCT level_config_id, level_id, fight_stage
          FROM abyss_level_monster_spawn) AS sp
      USING (level_config_id, level_id)
    LEFT JOIN rotation_state AS rs USING (level_config_id)
    WHERE l.level_config_id <> 'Abyss_Common'
    UNION ALL
    SELECT 'clone', cd.clone_id, CAST(cd.difficulty_ordinal AS TEXT), '', 'encounter',
           'clone', c.name_zh, CAST(cd.difficulty_ordinal AS TEXT), d.dataset_id, '',
           a.name_zh, cd.clone_id, COALESCE(c.clone_type, ''), '', a.source_row_id,
           '40:' || printf('%04d', COALESCE(c.ordinal, 9999)) || ':' || cd.clone_id || ':' ||
             printf('%04d', cd.difficulty_ordinal),
           lower(COALESCE(a.name_zh, '') || ' ' || COALESCE(c.name_zh, '') || ' ' ||
                 cd.clone_id || ' ' || COALESCE(c.clone_type, '') || ' ' ||
                 COALESCE(cd.spawn_id, '') || ' ' ||
                 COALESCE((SELECT group_concat(sm.monster_template_path, ' ')
                           FROM clone_spawn_member AS sm
                           WHERE sm.spawn_id = cd.spawn_id), ''))
    FROM clone_activity_difficulty AS cd
    LEFT JOIN clone_activity AS a USING (clone_id)
    LEFT JOIN clone_activity_category AS c USING (category_id)
    CROSS JOIN dataset AS d
    UNION ALL
    SELECT 'high_risk', h.commission_id, CAST(hd.difficulty_id AS TEXT), '',
           'encounter', 'high_risk', '高危委托', CAST(hd.difficulty_id AS TEXT),
           d.dataset_id, '', h.name_zh, h.commission_id,
           COALESCE(hd.monster_pool_id, h.fallback_monster_pool_id, ''), '', h.source_row_id,
           '50:' || h.commission_id || ':' || printf('%04d', hd.difficulty_id),
           lower(COALESCE(h.name_zh, '') || ' ' || h.commission_id || ' ' ||
                 COALESCE(hd.scene_data_id, '') || ' ' || COALESCE(hd.monster_pool_id, '') || ' ' ||
                 COALESCE((SELECT group_concat(hm.monster_class_path, ' ')
                           FROM high_risk_monster_pool_member AS hm
                           WHERE hm.monster_pool_id = hd.monster_pool_id), ''))
    FROM high_risk_commission AS h
    JOIN high_risk_commission_difficulty AS hd USING (commission_id)
    CROSS JOIN dataset AS d
)
"""


def _numeric_identity(value: object) -> tuple[str, int] | None:
    parts = str(value or "").strip().casefold().split("_")
    if len(parts) < 2 or parts[0] not in {"mon", "boss"}:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


class StaticCatalogMonsterQueries(
    StaticCatalogHistoricalFeastQueriesMixin,
    StaticGameDataDao,
):
    """Read-only catalog DAO kept separate from the shared static DAO surface."""

    @staticmethod
    def _like_value(value: str) -> str:
        escaped = str(value).strip().casefold().replace("\\", "\\\\")
        escaped = escaped.replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def catalog_metadata(self) -> dict[str, Any]:
        row = self._one(
            "SELECT dataset_id, importer_version, built_at_utc FROM dataset"
        )
        return row or {}

    def list_divination_buffs(self) -> list[dict[str, Any]]:
        """Return formal blessing choices with exact GE identity relations."""

        return self._rows(
            """
            SELECT b.buff_id, b.name_zh, b.description_zh, b.property_id,
                   b.property_value, b.is_percent, b.source_row_id,
                   ge.gameplay_effect_id AS mechanism_effect_id
            FROM divination_buff AS b
            LEFT JOIN gameplay_effect_catalog AS ge
              ON ge.gameplay_effect_id = b.buff_id COLLATE BINARY
            ORDER BY b.buff_id
            """
        )

    def divination_buff(self, buff_id: str) -> dict[str, Any] | None:
        row = self._one(
            """
            SELECT b.buff_id, b.name_zh, b.description_zh, b.property_id,
                   b.property_value, b.is_percent, b.source_row_id,
                   ge.gameplay_effect_id AS mechanism_effect_id
            FROM divination_buff AS b
            LEFT JOIN gameplay_effect_catalog AS ge
              ON ge.gameplay_effect_id = b.buff_id COLLATE BINARY
            WHERE b.buff_id = ? COLLATE BINARY
            """,
            (str(buff_id),),
        )
        if row is not None:
            row["source"] = self.source_trace(row.get("source_row_id"))
        return row

    def outer_realm_season_buff(
        self,
        level_config_id: str,
    ) -> dict[str, Any] | None:
        """Return one season Buff and its structured components."""

        row = self._one(
            """
            SELECT b.level_config_id, b.season_name_zh, b.buff_id,
                   b.buff_name_zh, b.description_zh, b.add_to_character,
                   b.season_source_row_id, b.buff_source_row_id,
                   ge.gameplay_effect_id AS mechanism_effect_id
            FROM outer_realm_season_buff AS b
            LEFT JOIN gameplay_effect_catalog AS ge
              ON ge.class_path = b.gameplay_effect_path COLLATE BINARY
            WHERE b.level_config_id = ? COLLATE BINARY
            """,
            (str(level_config_id),),
        )
        if row is None:
            return None
        row["components"] = self._rows(
            """
            SELECT component_ordinal, trigger_kind, property_id,
                   property_value, duration_seconds,
                   trigger_cooldown_seconds, stack_limit_count
            FROM outer_realm_season_buff_component
            WHERE level_config_id = ? COLLATE BINARY
            ORDER BY component_ordinal
            """,
            (str(level_config_id),),
        )
        row["source"] = self.source_trace(row.get("buff_source_row_id"))
        return row

    def clone_drop_projection(self, drop_id: object) -> dict[str, Any] | None:
        """Return the v30 drop closure without exposing its internal key."""

        identity = str(drop_id or "").strip()
        if not identity:
            return None
        row = self._one(
            """SELECT status, reason_code
               FROM clone_drop_projection WHERE drop_id = ? COLLATE BINARY""",
            (identity,),
        )
        if row is None:
            return None
        row["items"] = self._rows(
            """
            SELECT item_id, quantity
            FROM clone_drop_projection_item
            WHERE drop_id = ? COLLATE BINARY
            ORDER BY item_id
            """,
            (identity,),
        )
        row["gaps"] = self._rows(
            """
            SELECT reason_code
            FROM clone_drop_projection_gap
            WHERE drop_id = ? COLLATE BINARY
            ORDER BY ordinal
            """,
            (identity,),
        )
        return row

    def clone_drop_status_counts(self) -> dict[str, int]:
        rows = self._rows(
            """
            SELECT COALESCE(p.status, 'unavailable') AS status, COUNT(*) AS count
            FROM clone_activity_difficulty AS d
            LEFT JOIN clone_drop_projection AS p ON p.drop_id = d.drop_id
            GROUP BY COALESCE(p.status, 'unavailable') ORDER BY status
            """
        )
        return {str(row["status"]): int(row["count"]) for row in rows}

    def next_outer_realm_start(self, as_of_mainland: str) -> str | None:
        row = self._one(
            "SELECT MIN(starts_at_mainland) AS starts FROM outer_realm_rotation "
            "WHERE starts_at_mainland > ?",
            (str(as_of_mainland),),
        )
        return None if row is None or row.get("starts") is None else str(row["starts"])

    def list_catalog_index(
        self,
        *,
        search: str = "",
        domain: str = "all",
        play_mode: str = "all",
        region: str = "",
        difficulty: str = "",
        version: str = "",
        release_scope: str = "all",
        as_of_mainland: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        safe_limit = max(1, min(int(limit), 200))
        safe_offset = max(0, int(offset))
        filters = """
        WHERE (:domain = 'all' OR domain = :domain)
          AND (:play_mode = 'all' OR play_mode = :play_mode)
          AND (:search = '' OR search_text LIKE :search_like ESCAPE '\\')
          AND (:region = '' OR lower(region) LIKE :region_like ESCAPE '\\')
          AND (:difficulty = '' OR lower(difficulty) LIKE :difficulty_like ESCAPE '\\')
          AND (:version = '' OR lower(version_id) LIKE :version_like ESCAPE '\\'
               OR lower(release_state) LIKE :version_like ESCAPE '\\')
          AND (:release_scope = 'all'
               OR (play_mode = 'outer_realm' AND (
                   release_state = :release_scope
                   OR (:release_scope = 'current_next'
                       AND release_state IN ('current', 'next')))))
        """
        parameters: dict[str, Any] = {
            "as_of": str(as_of_mainland),
            "domain": domain if domain in {"all", "monster", "encounter"} else "all",
            "play_mode": str(play_mode or "all"),
            "search": str(search).strip(),
            "search_like": self._like_value(search),
            "region": str(region).strip(),
            "region_like": self._like_value(region),
            "difficulty": str(difficulty).strip(),
            "difficulty_like": self._like_value(difficulty),
            "version": str(version).strip(),
            "version_like": self._like_value(version),
            "release_scope": (
                release_scope
                if release_scope in {"all", "current", "next", "current_next"}
                else "all"
            ),
            "limit": safe_limit,
            "offset": safe_offset,
        }
        if self._connection is None:
            raise RuntimeError("静态数据库 DAO 已关闭")
        total = int(
            self._connection.execute(
                _INDEX_CTE + "SELECT COUNT(*) AS count FROM catalog_entry " + filters,
                parameters,
            ).fetchone()[0]
        )
        rows = [
            dict(row)
            for row in self._connection.execute(
                _INDEX_CTE
                + "SELECT * FROM catalog_entry "
                + filters
                + " ORDER BY sort_key LIMIT :limit OFFSET :offset",
                parameters,
            )
        ]
        return rows, total

    def source_trace(self, source_row_id: int | None) -> dict[str, Any] | None:
        if source_row_id is None:
            return None
        return self._one(
            """
            SELECT r.source_row_id, f.relative_path, f.sha256 AS source_file_sha256,
                   f.row_count AS source_file_row_count, r.row_key,
                   r.content_sha256, r.payload_json IS NOT NULL AS payload_available
            FROM source_row AS r
            JOIN source_file AS f USING (source_file_id)
            WHERE r.source_row_id = ?
            """,
            (int(source_row_id),),
        )

    def combat_profile(self, profile_set: str, pack_id: str) -> dict[str, Any] | None:
        profile = self._one(
            """
            SELECT profile_set, pack_id, defense_base, defense_up, defense_add,
                   defense_ignore, topple_limit, topple_accrue_efficiency,
                   topple_anti_accrue_efficiency, topple_bonus,
                   topple_reduce_natural, topple_reduce_reset,
                   health_base, health_up, health_add, source_row_id
            FROM enemy_combat_profile
            WHERE profile_set = ? AND pack_id = ?
            """,
            (str(profile_set), str(pack_id)),
        )
        if profile is None:
            return None
        profile["resistances"] = self._rows(
            """
            SELECT damage_type, resistance_base, immunity
            FROM enemy_element_resistance
            WHERE profile_set = ? AND pack_id = ?
            ORDER BY damage_type
            """,
            (str(profile_set), str(pack_id)),
        )
        profile["source"] = self.source_trace(profile.get("source_row_id"))
        return profile

    def manual_monster(self, monster_manual_id: str) -> dict[str, Any] | None:
        monster = self._one(
            "SELECT * FROM monster_catalog WHERE monster_manual_id = ?",
            (str(monster_manual_id),),
        )
        if monster is None:
            return None
        monster["source"] = self.source_trace(monster.get("source_row_id"))
        monster["aliases"] = self._rows(
            """SELECT alias_kind, ordinal, alias_value
               FROM monster_identifier_alias WHERE monster_manual_id = ?
               ORDER BY alias_kind, ordinal""",
            (str(monster_manual_id),),
        )
        bindings = self._rows(
            """SELECT monster_template_name, binding_kind, source_row_id
               FROM monster_template_binding WHERE monster_manual_id = ?
               ORDER BY binding_kind, monster_template_name""",
            (str(monster_manual_id),),
        )
        for binding in bindings:
            binding["source"] = self.source_trace(binding.get("source_row_id"))
            binding["profiles"] = self._rows(
                """
                SELECT static_table, monster_id, monster_level,
                       default_profile_set, default_pack_id, online_ratio_id,
                       source_row_id
                FROM monster_instance_profile
                WHERE lower(monster_id) = lower(?)
                ORDER BY static_table, monster_id
                """,
                (binding["monster_template_name"],),
            )
        monster["bindings"] = bindings
        return monster

    def profile_monster(self, static_table: str, monster_id: str) -> dict[str, Any] | None:
        profile = self._one(
            """
            SELECT static_table, monster_id, monster_level,
                   default_profile_set, default_pack_id, online_ratio_id,
                   source_row_id
            FROM monster_instance_profile
            WHERE static_table = ? AND monster_id = ?
            """,
            (str(static_table), str(monster_id)),
        )
        if profile is None:
            return None
        profile["source"] = self.source_trace(profile.get("source_row_id"))
        profile["manual_bindings"] = self._rows(
            """
            SELECT b.monster_manual_id, b.binding_kind, b.source_row_id,
                   m.name_zh, m.enemy_type, m.place_zh
            FROM monster_template_binding AS b
            JOIN monster_catalog AS m USING (monster_manual_id)
            WHERE lower(b.monster_template_name) = lower(?)
            ORDER BY b.binding_kind, b.monster_manual_id
            """,
            (str(monster_id),),
        )
        profile["variants"] = self._rows(
            """
            SELECT variant_kind, threshold_level, profile_set, pack_id
            FROM monster_instance_profile_variant
            WHERE static_table = ? AND monster_id = ?
            ORDER BY variant_kind, threshold_level, profile_set, pack_id
            """,
            (str(static_table), str(monster_id)),
        )
        return profile

    def template_profile_candidates(self, monster_template_name: str) -> list[dict[str, Any]]:
        profiles = self._rows(
            """
            SELECT static_table, monster_id, monster_level,
                   default_profile_set, default_pack_id, online_ratio_id,
                   source_row_id
            FROM monster_instance_profile
            WHERE lower(monster_id) = lower(?)
            ORDER BY CASE static_table
                         WHEN 'monster_static_big_world_gameplay' THEN 0
                         WHEN 'monster_static_big_world' THEN 1
                         ELSE 2 END,
                     static_table, monster_id
            """,
            (str(monster_template_name),),
        )
        for profile in profiles:
            profile["source"] = self.source_trace(profile.get("source_row_id"))
            profile["variants"] = self._rows(
                """
                SELECT variant_kind, threshold_level, profile_set, pack_id
                FROM monster_instance_profile_variant
                WHERE static_table = ? AND monster_id = ?
                ORDER BY variant_kind, threshold_level, profile_set, pack_id
                """,
                (profile["static_table"], profile["monster_id"]),
            )
        return profiles

    def profile_family_candidates(
        self, monster_template_name: str,
    ) -> list[dict[str, Any]]:
        """Return profiles sharing the explicit mon/boss numeric ID family."""

        parts = str(monster_template_name).strip().casefold().split("_")
        if len(parts) < 2 or parts[0] not in {"mon", "boss"}:
            return []
        try:
            ordinal = int(parts[1])
        except ValueError:
            return []
        rows = self._rows(
            """SELECT static_table, monster_id
               FROM monster_instance_profile
               WHERE lower(monster_id) LIKE ?
               ORDER BY static_table, monster_id""",
            (f"{parts[0]}_%",),
        )
        return [
            row for row in rows
            if _numeric_identity(row.get("monster_id")) == (parts[0], ordinal)
        ]

    def template_encounter_references(
        self, monster_template_name: str,
    ) -> list[dict[str, Any]]:
        """Return only explicit ID/path references from a template to gameplay rows."""

        template_name = str(monster_template_name)
        return self._rows(
            """
            SELECT 'world_boss' AS entity_kind,
                   m.monster_manual_id AS identity_1, '' AS identity_2,
                   '' AS identity_3, 'explicit_template_binding' AS relation_kind,
                   m.name_zh AS title_zh
            FROM monster_template_binding AS b
            JOIN monster_catalog AS m USING (monster_manual_id)
            WHERE lower(b.monster_template_name) = lower(?)
              AND b.binding_kind = 'world_boss_id'
            UNION ALL
            SELECT 'feast', s.stage_id, CAST(d.difficulty_id AS TEXT), '',
                   'exact_official_template_id', s.name_zh
            FROM feast_stage AS s
            JOIN feast_stage_difficulty AS d USING (stage_id)
            WHERE lower(s.boss_monster_id) = lower(?)
            UNION ALL
            SELECT 'clone', d.clone_id, CAST(d.difficulty_ordinal AS TEXT), '',
                   'exact_official_template_id', a.name_zh
            FROM clone_spawn_member AS m
            JOIN clone_activity_difficulty AS d USING (spawn_id)
            LEFT JOIN clone_activity AS a USING (clone_id)
            WHERE lower(m.monster_template_name) = lower(?)
            UNION ALL
            SELECT 'high_risk', d.commission_id, CAST(d.difficulty_id AS TEXT), '',
                   'exact_official_template_id', c.name_zh
            FROM high_risk_monster_pool_member AS m
            JOIN high_risk_commission_difficulty AS d USING (monster_pool_id)
            JOIN high_risk_commission AS c USING (commission_id)
            WHERE lower(m.monster_template_name) = lower(?)
            UNION ALL
            SELECT 'outer_realm', s.level_config_id, CAST(s.level_id AS TEXT),
                   s.fight_stage, 'exact_class_path_object', l.name_zh
            FROM abyss_monster_pool_entry AS p
            JOIN abyss_level_monster_spawn AS s USING (monster_pool_id)
            JOIN abyss_level AS l USING (level_config_id, level_id)
            WHERE lower(p.monster_class_path) LIKE '%.' || lower(?) || '_c'
               OR lower(p.monster_class_path) LIKE '%.' || lower(?)
            ORDER BY entity_kind, identity_1, identity_2, identity_3
            """,
            (
                template_name,
                template_name,
                template_name,
                template_name,
                template_name,
                template_name,
            ),
        )

    def feast_encounter(self, stage_id: str, difficulty_id: int) -> dict[str, Any] | None:
        row = self._one(
            """
            SELECT s.stage_id, s.name_zh, s.boss_monster_id,
                   s.special_high_difficulty, s.source_row_id,
                   d.difficulty_id, d.name_zh AS difficulty_name_zh,
                   d.boss_name_zh, d.base_score, d.score_rate,
                   d.monster_level, d.profile_set, d.pack_id, d.boss_icon_path
            FROM feast_stage AS s
            JOIN feast_stage_difficulty AS d USING (stage_id)
            WHERE s.stage_id = ? AND d.difficulty_id = ?
            """,
            (str(stage_id), int(difficulty_id)),
        )
        if row is None:
            return None
        row["source"] = self.source_trace(row.get("source_row_id"))
        row["profile"] = self.combat_profile(row["profile_set"], row["pack_id"])
        row["options"] = self._rows(
            """
            SELECT so.category_ordinal, so.option_ordinal, so.category_name_zh,
                   o.option_id, o.option_type, o.effect_kind, o.damage_type,
                   o.add_value, o.limit_seconds, o.score, o.buff_asset_path,
                   o.source_row_id,
                   ge.gameplay_effect_id AS mechanism_effect_id
            FROM feast_stage_option AS so
            JOIN feast_option AS o USING (option_id)
            LEFT JOIN gameplay_effect_catalog AS ge
              ON ge.class_path = o.buff_asset_path COLLATE BINARY
            WHERE so.stage_id = ?
            ORDER BY so.category_ordinal, so.option_ordinal
            """,
            (str(stage_id),),
        )
        return row

    def feast_setup(self, stage_id: str) -> dict[str, Any] | None:
        """Return one formal stage with all selectable difficulty and rule choices."""

        stage = self._one(
            """
            SELECT stage_id, name_zh, boss_monster_id, special_high_difficulty
            FROM feast_stage WHERE stage_id = ?
            """,
            (str(stage_id),),
        )
        if stage is None:
            return None
        stage["difficulties"] = self._rows(
            """
            SELECT difficulty_id, name_zh, boss_name_zh, score_rate,
                   monster_level, boss_icon_path
            FROM feast_stage_difficulty
            WHERE stage_id = ? ORDER BY difficulty_id
            """,
            (str(stage_id),),
        )
        stage["options"] = self._rows(
            """
            SELECT so.category_ordinal, so.category_name_zh, so.option_ordinal,
                   o.option_id, o.effect_kind, o.damage_type, o.add_value,
                   o.limit_seconds, o.score,
                   ge.gameplay_effect_id AS mechanism_effect_id
            FROM feast_stage_option AS so
            JOIN feast_option AS o USING (option_id)
            LEFT JOIN gameplay_effect_catalog AS ge
              ON ge.class_path = o.buff_asset_path COLLATE BINARY
            WHERE so.stage_id = ?
            ORDER BY so.category_ordinal, so.option_ordinal
            """,
            (str(stage_id),),
        )
        return stage

    def outer_realm_encounter(
        self, level_config_id: str, level_id: int, fight_stage: str,
    ) -> dict[str, Any] | None:
        level = self._one(
            """
            SELECT l.level_config_id, l.level_id, l.abyss_id, l.name_zh,
                   l.source_row_id, r.starts_at_mainland, r.ends_at_mainland,
                   r.inference_ordinal
            FROM abyss_level AS l
            LEFT JOIN outer_realm_rotation AS r USING (level_config_id)
            WHERE l.level_config_id = ? AND l.level_id = ?
            """,
            (str(level_config_id), int(level_id)),
        )
        if level is None:
            return None
        level["source"] = self.source_trace(level.get("source_row_id"))
        level["fight_stage"] = str(fight_stage)
        members = self._rows(
            """
            SELECT s.spawn_ordinal, s.wave, s.monster_pool_id,
                   s.next_spawn_type, s.spawn_time, s.source_row_id AS spawn_source_row_id,
                   p.monster_ordinal, p.monster_class_path, p.monster_name_zh,
                   p.monster_count, p.monster_level,
                   p.attribute_profile_set AS profile_set,
                   p.attribute_pack_id AS pack_id, p.source_row_id
            FROM abyss_level_monster_spawn AS s
            JOIN abyss_monster_pool_entry AS p USING (monster_pool_id)
            WHERE s.level_config_id = ? AND s.level_id = ? AND s.fight_stage = ?
            ORDER BY s.spawn_ordinal, p.monster_ordinal
            """,
            (str(level_config_id), int(level_id), str(fight_stage)),
        )
        for member in members:
            member["source"] = self.source_trace(member.get("source_row_id"))
            member["profile"] = self.combat_profile(
                member["profile_set"], member["pack_id"]
            )
        level["members"] = members
        return level

    def outer_realm_member(
        self,
        level_config_id: str,
        level_id: int,
        fight_stage: str,
        spawn_ordinal: int,
        monster_pool_id: str,
        monster_ordinal: int,
    ) -> dict[str, Any] | None:
        """Return one exact formal spawn-pool member and its encounter profile."""

        row = self._one(
            """
            SELECT l.level_config_id, l.level_id, l.name_zh,
                   r.starts_at_mainland, r.ends_at_mainland,
                   s.fight_stage, s.spawn_ordinal, s.wave, s.monster_pool_id,
                   s.next_spawn_type, s.spawn_time,
                   p.monster_ordinal, p.monster_class_path, p.monster_name_zh,
                   p.monster_count, p.monster_level,
                   p.attribute_profile_set AS profile_set,
                   p.attribute_pack_id AS pack_id, p.source_row_id
            FROM abyss_level AS l
            JOIN abyss_level_monster_spawn AS s
              USING (level_config_id, level_id)
            JOIN abyss_monster_pool_entry AS p USING (monster_pool_id)
            LEFT JOIN outer_realm_rotation AS r USING (level_config_id)
            WHERE l.level_config_id = ? AND l.level_id = ?
              AND s.fight_stage = ? AND s.spawn_ordinal = ?
              AND s.monster_pool_id = ? AND p.monster_ordinal = ?
            """,
            (
                str(level_config_id), int(level_id), str(fight_stage),
                int(spawn_ordinal), str(monster_pool_id), int(monster_ordinal),
            ),
        )
        if row is None:
            return None
        row["source"] = self.source_trace(row.get("source_row_id"))
        row["profile"] = self.combat_profile(row["profile_set"], row["pack_id"])
        return row

    def clone_encounter(self, clone_id: str, difficulty_ordinal: int) -> dict[str, Any] | None:
        row = self._one(
            """
            SELECT d.clone_id, a.clone_type, a.category_id, a.name_zh,
                   a.description_zh, a.show_in_adventure, a.cross_scene,
                   a.source_row_id, c.name_zh AS category_name_zh,
                   c.ordinal AS category_ordinal, d.difficulty_ordinal,
                   d.difficulty_level, d.team_level, d.stamina_cost, d.drop_id,
                   d.spawn_id, d.kill_monster_time_limit
            FROM clone_activity_difficulty AS d
            LEFT JOIN clone_activity AS a USING (clone_id)
            LEFT JOIN clone_activity_category AS c USING (category_id)
            WHERE d.clone_id = ? AND d.difficulty_ordinal = ?
            """,
            (str(clone_id), int(difficulty_ordinal)),
        )
        if row is None:
            return None
        row["source"] = self.source_trace(row.get("source_row_id"))
        row["drop_projection"] = self.clone_drop_projection(row.get("drop_id"))
        row["members"] = self._rows(
            """
            SELECT wave_ordinal, entry_ordinal, monster_template_path,
                   monster_template_name, monster_count, source_row_id
            FROM clone_spawn_member WHERE spawn_id = ?
            ORDER BY wave_ordinal, entry_ordinal
            """,
            (row["spawn_id"],),
        ) if row.get("spawn_id") else []
        return row

    def high_risk_encounter(
        self, commission_id: str, difficulty_id: int,
    ) -> dict[str, Any] | None:
        row = self._one(
            """
            SELECT c.commission_id, c.name_zh, c.difficulty_count,
                   c.fallback_monster_pool_id, c.source_row_id,
                   d.difficulty_id, d.recommended_character_level,
                   d.scene_data_id, d.monster_pool_id
            FROM high_risk_commission AS c
            JOIN high_risk_commission_difficulty AS d USING (commission_id)
            WHERE c.commission_id = ? AND d.difficulty_id = ?
            """,
            (str(commission_id), int(difficulty_id)),
        )
        if row is None:
            return None
        row["source"] = self.source_trace(row.get("source_row_id"))
        pool_id = row.get("monster_pool_id")
        row["members"] = self._rows(
            """
            SELECT member_ordinal, monster_class_path, monster_template_name,
                   monster_count, configured_monster_level, attribute_id,
                   source_row_id
            FROM high_risk_monster_pool_member
            WHERE monster_pool_id = ? ORDER BY member_ordinal
            """,
            (pool_id,),
        ) if pool_id else []
        return row
