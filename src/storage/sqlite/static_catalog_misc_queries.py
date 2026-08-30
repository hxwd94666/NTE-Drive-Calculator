# 游戏资料库 B 域的装备、技能效果、资源和来源追溯只读查询。
"""Bounded read-only queries for the miscellaneous static catalog domains."""

from __future__ import annotations

import json
from typing import Any

from .protocols import StaticDataDaoMixinHost
from .static_game_data_dao import StaticGameDataDao


_MAX_PAGE_SIZE = 100
_MAX_QUERY_LENGTH = 200


def _equipment_entries_sql() -> str:
    return """
        SELECT 'equipment_item', item_id, COALESCE(NULLIF(name_zh, ''), item_id),
               kind || ' / ' || quality, source_row_id, NULL,
               item_id || ' ' || COALESCE(name_zh, '') || ' ' || kind || ' ' ||
                   quality || ' ' || COALESCE(suit_id, '') || ' ' ||
                   COALESCE(icon_path, '')
        FROM equipment_item
        UNION ALL
        SELECT 'equipment_suit', suit_id, COALESCE(NULLIF(name_zh, ''), suit_id),
               '卡带套装', source_row_id, NULL,
               suit_id || ' ' || COALESCE(name_zh, '') || ' ' ||
                   COALESCE(icon_path, '')
        FROM equipment_suit
        UNION ALL
        SELECT 'equipment_shape', shape_id, shape_id,
               '驱动形状 / ' || cell_count || ' 格', source_row_id, NULL,
               shape_id || ' ' || cell_count
        FROM equipment_shape
        UNION ALL
        SELECT 'equipment_attribute', attribute_id,
               COALESCE(NULLIF(display_name_zh, ''), attribute_id),
               COALESCE(NULLIF(filter_name_zh, ''), attribute_type),
               source_row_id, NULL,
               attribute_id || ' ' || COALESCE(display_name_zh, '') || ' ' ||
                   COALESCE(filter_name_zh, '') || ' ' ||
                   COALESCE(random_attribute_name_zh, '')
        FROM equipment_attribute
        UNION ALL
        SELECT 'equipment_curve', curve_id, curve_id,
               '装备主属性强化曲线', source_row_id, NULL, curve_id
        FROM equipment_base_attribute_curve
        UNION ALL
        SELECT 'equipment_buff_curve', curve_id, curve_id,
               '装备效果曲线', source_row_id, NULL, curve_id
        FROM equipment_buff_curve
        UNION ALL
        SELECT 'equipment_modify_pack', modify_pack_id, modify_pack_id,
               '装备属性修改包', source_row_id, NULL, modify_pack_id
        FROM equipment_modify_pack
        UNION ALL
        SELECT 'equipment_plan', CAST(plan.character_id AS TEXT),
               COALESCE(NULLIF(character.name_zh, ''), CAST(plan.character_id AS TEXT)),
               '官方装备图纸', plan.source_row_id, NULL,
               CAST(plan.character_id AS TEXT) || ' ' ||
                   COALESCE(character.name_zh, '') || ' ' || plan.core_item_id
        FROM equipment_plan AS plan
        LEFT JOIN character USING (character_id)
        UNION ALL
        SELECT 'graduation_template', CAST(template.character_id AS TEXT),
               COALESCE(NULLIF(character.name_zh, ''), CAST(template.character_id AS TEXT)),
               '项目毕业模板 / ' || template.source_kind, NULL, NULL,
               CAST(template.character_id AS TEXT) || ' ' ||
                   COALESCE(character.name_zh, '') || ' ' ||
                   COALESCE(template.fork_id, '') || ' ' ||
                   COALESCE(template.core_suit_id, '')
        FROM character_graduation_template AS template
        LEFT JOIN character USING (character_id)
    """


def _skill_entries_sql() -> str:
    return """
        SELECT 'gameplay_ability', ability_id,
               COALESCE(NULLIF(name_zh, ''), ability_id),
               'Gameplay Ability', source_row_id, NULL,
               ability_id || ' ' || COALESCE(name_zh, '') || ' ' ||
                   COALESCE(gameplay_ability_path, '') || ' ' ||
                   COALESCE(icon_path, '')
        FROM gameplay_ability_catalog
        UNION ALL
        SELECT 'skill_damage', damage.damage_id,
               damage.damage_id,
               COALESCE(NULLIF(ability.name_zh, ''), damage.ability_id, '技能伤害项'),
               damage.source_row_id, NULL,
               damage.damage_id || ' ' || COALESCE(damage.ability_id, '') || ' ' ||
                   COALESCE(ability.name_zh, '') || ' ' || damage.damage_type || ' ' ||
                   damage.damage_source_category
        FROM skill_damage AS damage
        LEFT JOIN gameplay_ability_catalog AS ability
          ON ability.ability_id = damage.ability_id
    """


def _effect_entries_sql() -> str:
    return """
        SELECT 'gameplay_effect', gameplay_effect_id, gameplay_effect_id,
               'Gameplay Effect #' || gameplay_effect_index,
               source_row_id, NULL,
               gameplay_effect_id || ' ' || class_path || ' ' ||
                   gameplay_effect_index
        FROM gameplay_effect_catalog
        UNION ALL
        SELECT 'buff', asset_path,
               COALESCE(NULLIF(definition_id, ''), asset_path),
               COALESCE(definition_kind, 'Buff') || ' / ' ||
                   COALESCE(duration_policy, '持续规则未知'),
               NULL, source_file_id,
               asset_path || ' ' || COALESCE(definition_id, '') || ' ' ||
                   COALESCE(definition_kind, '') || ' ' ||
                   COALESCE(duration_policy, '') || ' ' ||
                   COALESCE(stacking_type, '')
        FROM buff_definition
        UNION ALL
        SELECT 'combat_effect', effect_definition_id, effect_definition_id,
               owner_kind || ' / ' || effect_kind,
               source_row_id, NULL,
               effect_definition_id || ' ' || owner_kind || ' ' || owner_id || ' ' ||
                   effect_kind || ' ' || activation_kind || ' ' ||
                   COALESCE(description_zh, '')
        FROM combat_effect_definition
        UNION ALL
        SELECT 'combat_curve', curve_table_asset_path || char(31) || curve_id, curve_id,
               '战斗曲线 / ' || interpolation_mode, source_row_id, NULL,
               curve_id || ' ' || curve_table_asset_path || ' ' || interpolation_mode
        FROM combat_curve
        UNION ALL
        SELECT 'combat_level_curve', curve_id, curve_id,
               COALESCE(damage_kind, '等级曲线') || ' / ' ||
                   COALESCE(reaction_type, '非反应'),
               source_row_id, NULL,
               curve_id || ' ' || COALESCE(damage_kind, '') || ' ' ||
                   COALESCE(reaction_type, '') || ' ' ||
                   COALESCE(source_effect_id, '')
        FROM combat_level_curve
        UNION ALL
        SELECT 'reaction', reaction_type, reaction_type,
               element_type_1 || ' + ' || element_type_2,
               source_row_id, NULL,
               reaction_type || ' ' || element_type_1 || ' ' || element_type_2 || ' ' ||
                   default_damage_effect_id
        FROM reaction_definition
        UNION ALL
        SELECT 'combat_constant', constant_id, constant_id,
               COALESCE(description_zh, '战斗常量'), source_row_id, NULL,
               constant_id || ' ' || COALESCE(description_zh, '') || ' ' ||
                   COALESCE(unit, '')
        FROM combat_effect_constant
        UNION ALL
        SELECT DISTINCT 'gameplay_tag', source_asset_path || char(31) || tag_name,
               tag_name, 'Gameplay Tag / ' || source_asset_path,
               NULL, NULL, tag_name || ' ' || source_asset_path
        FROM combat_blueprint_tag
        UNION ALL
        SELECT 'roguelike_modifier', profile.modifier_id, profile.modifier_id,
               '玩法属性包 / ' || (
                   SELECT COUNT(*) FROM roguelike_modifier_property AS property
                   WHERE property.modifier_id = profile.modifier_id
               ) || ' 项',
               profile.source_row_id, NULL,
               profile.modifier_id || ' ' || profile.conditions_json || ' ' ||
                   COALESCE((
                       SELECT GROUP_CONCAT(property.property_id, ' ')
                       FROM roguelike_modifier_property AS property
                       WHERE property.modifier_id = profile.modifier_id
                   ), '')
        FROM roguelike_modifier_profile AS profile
    """


def _asset_entries_sql() -> str:
    return """
        SELECT 'blueprint', asset_path,
               COALESCE(NULLIF(asset_name, ''), asset_path),
               asset_kind || ' / ' || asset_type,
               NULL, source_file_id,
               asset_path || ' ' || asset_name || ' ' || asset_kind || ' ' || asset_type
        FROM combat_blueprint_asset
        UNION ALL
        SELECT 'montage', asset_path, asset_path, 'Montage', NULL, NULL, asset_path
        FROM combat_montage
    """


def _source_entries_sql() -> str:
    return """
        SELECT 'source_file', CAST(source_file_id AS TEXT), relative_path,
               row_count || ' 个来源行', NULL, source_file_id,
               CAST(source_file_id AS TEXT) || ' ' || relative_path || ' ' || sha256
        FROM source_file
        UNION ALL
        SELECT 'source_row', CAST(row.source_row_id AS TEXT), row.row_key,
               file.relative_path, row.source_row_id, row.source_file_id,
               CAST(row.source_row_id AS TEXT) || ' ' || row.row_key || ' ' ||
                   row.content_sha256 || ' ' || file.relative_path
        FROM source_row AS row
        JOIN source_file AS file USING (source_file_id)
    """


_SEARCH_SQL = {
    "equipment": _equipment_entries_sql(),
    "skills": _skill_entries_sql(),
    "effects": _effect_entries_sql(),
    "assets": _asset_entries_sql(),
    "sources": _source_entries_sql(),
}
_SEARCH_SQL["all"] = " UNION ALL ".join(
    f"SELECT * FROM ({sql})" for sql in _SEARCH_SQL.values()
)


def _json_value(value: object, fallback: object) -> object:
    if value in (None, ""):
        return fallback
    return json.loads(str(value))


class StaticCatalogMiscQueriesMixin(StaticDataDaoMixinHost):
    """Expose only product-owned catalog queries; arbitrary SQL is unsupported."""

    @staticmethod
    def _pagination(limit: int, offset: int) -> tuple[int, int]:
        normalized_limit = int(limit)
        normalized_offset = int(offset)
        if not 1 <= normalized_limit <= _MAX_PAGE_SIZE:
            raise ValueError(f"limit 必须在 1 到 {_MAX_PAGE_SIZE} 之间")
        if normalized_offset < 0:
            raise ValueError("offset 不能为负数")
        return normalized_limit, normalized_offset

    @staticmethod
    def _search_term(query: str) -> tuple[str, str]:
        normalized = str(query).strip()
        if len(normalized) > _MAX_QUERY_LENGTH:
            raise ValueError(f"搜索词不能超过 {_MAX_QUERY_LENGTH} 个字符")
        escaped = (
            normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        return normalized, f"%{escaped}%"

    def search_catalog_entries(
        self,
        domain_key: str,
        query: str = "",
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search a whitelisted product domain with bounded parameterized SQL."""

        entries_sql = _SEARCH_SQL.get(str(domain_key))
        if entries_sql is None:
            raise ValueError(f"不支持的资料库领域：{domain_key!r}")
        page_size, page_offset = self._pagination(limit, offset)
        exact, pattern = self._search_term(query)
        cte = f"""
            WITH entries(
                entity_kind, entity_key, title, subtitle,
                source_row_id, source_file_id, search_text
            ) AS ({entries_sql}),
            filtered AS (
                SELECT entity_kind, entity_key, title, subtitle,
                       source_row_id, source_file_id,
                       CASE
                           WHEN lower(entity_key) = lower(?)
                             OR lower(title) = lower(?) THEN 0
                           ELSE 1
                       END AS match_rank
                FROM entries
                WHERE ? = '' OR lower(search_text) LIKE lower(?) ESCAPE '\\'
            )
        """
        parameters = (exact, exact, exact, pattern)
        count = self._one(cte + "SELECT COUNT(*) AS count FROM filtered", parameters)
        rows = self._rows(
            cte
            + """
                SELECT entity_kind, entity_key, title, subtitle,
                       source_row_id, source_file_id
                FROM filtered
                ORDER BY match_rank, lower(title), lower(entity_key)
                LIMIT ? OFFSET ?
            """,
            (*parameters, page_size, page_offset),
        )
        return {
            "domain_key": str(domain_key),
            "query": exact,
            "limit": page_size,
            "offset": page_offset,
            "total": int((count or {}).get("count", 0)),
            "items": rows,
        }

    def catalog_domain_counts(self) -> dict[str, int]:
        """Return product-domain entity counts without exposing table browsing."""

        counts: dict[str, int] = {}
        for domain_key, entries_sql in _SEARCH_SQL.items():
            if domain_key == "all":
                continue
            row = self._one(f"SELECT COUNT(*) AS count FROM ({entries_sql})")
            counts[domain_key] = int((row or {}).get("count", 0))
        return counts

    def get_source_trace(
        self,
        *,
        source_row_id: int | None = None,
        source_file_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Resolve retained file/row provenance without promising source payloads."""

        if source_row_id is not None:
            return self._one(
                """
                SELECT row.source_row_id, row.row_key, row.content_sha256,
                       row.payload_json IS NOT NULL AS payload_present,
                       file.source_file_id, file.relative_path,
                       file.sha256 AS source_file_sha256, file.row_count
                FROM source_row AS row
                JOIN source_file AS file USING (source_file_id)
                WHERE row.source_row_id = ?
                """,
                (int(source_row_id),),
            )
        if source_file_id is not None:
            return self._one(
                """
                SELECT NULL AS source_row_id, NULL AS row_key,
                       NULL AS content_sha256, 0 AS payload_present,
                       source_file_id, relative_path,
                       sha256 AS source_file_sha256, row_count
                FROM source_file WHERE source_file_id = ?
                """,
                (int(source_file_id),),
            )
        raise ValueError("source_row_id 和 source_file_id 至少提供一个")

    def list_source_file_rows(
        self,
        source_file_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        page_size, page_offset = self._pagination(limit, offset)
        count = self._one(
            "SELECT COUNT(*) AS count FROM source_row WHERE source_file_id = ?",
            (int(source_file_id),),
        )
        rows = self._rows(
            """
            SELECT source_row_id, row_key, content_sha256,
                   payload_json IS NOT NULL AS payload_present
            FROM source_row WHERE source_file_id = ?
            ORDER BY source_row_id LIMIT ? OFFSET ?
            """,
            (int(source_file_id), page_size, page_offset),
        )
        return {
            "total": int((count or {}).get("count", 0)),
            "limit": page_size,
            "offset": page_offset,
            "items": rows,
        }

    def get_equipment_catalog_detail(
        self,
        entity_kind: str,
        entity_key: str,
    ) -> dict[str, Any] | None:
        """Return one equipment-family entity from an explicit kind whitelist."""

        key = str(entity_key).strip()
        if not key:
            raise ValueError("entity_key 不能为空")
        if entity_kind == "equipment_item":
            row = self._one("SELECT * FROM equipment_item WHERE item_id = ?", (key,))
            if row is not None:
                row["strength_levels"] = self._rows(
                    """
                    SELECT level, need_exp, source_row_id
                    FROM equipment_strength_level
                    WHERE strength_pack_id = ? ORDER BY level
                    """,
                    (row.get("strength_pack_id"),),
                )
            return row
        if entity_kind == "equipment_suit":
            row = self._one("SELECT * FROM equipment_suit WHERE suit_id = ?", (key,))
            if row is not None:
                row["required_shapes"] = self._rows(
                    """
                    SELECT ordinal, shape_id FROM equipment_suit_required_shape
                    WHERE suit_id = ? ORDER BY ordinal
                    """,
                    (key,),
                )
                row["effects"] = self._rows(
                    """
                    SELECT required_count, modify_pack_id, buff_object_path,
                           description_zh, reapply_after_revive, source_row_id
                    FROM equipment_suit_effect
                    WHERE suit_id = ? ORDER BY required_count
                    """,
                    (key,),
                )
            return row
        if entity_kind == "equipment_shape":
            row = self._one("SELECT * FROM equipment_shape WHERE shape_id = ?", (key,))
            if row is not None:
                row["cells"] = self._rows(
                    """
                    SELECT ordinal, x, y FROM equipment_shape_cell
                    WHERE shape_id = ? ORDER BY ordinal
                    """,
                    (key,),
                )
            return row
        if entity_kind == "equipment_attribute":
            row = self._one(
                "SELECT * FROM equipment_attribute WHERE attribute_id = ?", (key,)
            )
            if row is not None:
                row["curves"] = self._rows(
                    """
                    SELECT curve_id, interpolation_mode, default_value, source_row_id
                    FROM equipment_base_attribute_curve
                    WHERE curve_id = ? OR curve_id LIKE ? ESCAPE '\\'
                    ORDER BY curve_id
                    """,
                    (key, self._literal_prefix(key) + "%"),
                )
                row["core_random_attribute"] = self._one(
                    """
                    SELECT content_zh, content_text_table, content_text_key,
                           source_row_id
                    FROM equipment_core_random_attribute
                    WHERE attribute_id = ?
                    """,
                    (key,),
                )
            return row
        if entity_kind == "equipment_curve":
            row = self._one(
                "SELECT * FROM equipment_base_attribute_curve WHERE curve_id = ?", (key,)
            )
            if row is not None:
                row["points"] = self._rows(
                    """
                    SELECT ordinal, level, value FROM equipment_base_attribute_point
                    WHERE curve_id = ? ORDER BY ordinal
                    """,
                    (key,),
                )
            return row
        if entity_kind == "equipment_buff_curve":
            row = self._one("SELECT * FROM equipment_buff_curve WHERE curve_id = ?", (key,))
            if row is not None:
                row["points"] = self._rows(
                    """
                    SELECT ordinal, source_time, value FROM equipment_buff_curve_point
                    WHERE curve_id = ? ORDER BY ordinal
                    """,
                    (key,),
                )
            return row
        if entity_kind == "equipment_modify_pack":
            row = self._one(
                "SELECT * FROM equipment_modify_pack WHERE modify_pack_id = ?", (key,)
            )
            if row is not None:
                row["conditions"] = _json_value(row.pop("conditions_json"), [])
                row["modifiers"] = self._rows(
                    """
                    SELECT ordinal, property_id, value, operation, sort_key
                    FROM equipment_modify_value
                    WHERE modify_pack_id = ? ORDER BY ordinal
                    """,
                    (key,),
                )
            return row
        if entity_kind == "equipment_plan":
            try:
                character_id = int(key)
            except ValueError as exc:
                raise ValueError("装备图纸 key 必须是角色正式 ID") from exc
            return self.get_equipment_plan(character_id)
        if entity_kind == "graduation_template":
            try:
                character_id = int(key)
            except ValueError as exc:
                raise ValueError("毕业模板 key 必须是角色正式 ID") from exc
            row = self.get_character_graduation_template(character_id)
            if row is not None:
                character = self._one(
                    "SELECT name_zh FROM character WHERE character_id = ?",
                    (character_id,),
                )
                row["character_name_zh"] = (character or {}).get("name_zh")
            return row
        raise ValueError(f"不支持的装备详情类型：{entity_kind!r}")

    @staticmethod
    def _literal_prefix(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def get_effect_catalog_detail(
        self,
        entity_kind: str,
        entity_key: str,
    ) -> dict[str, Any] | None:
        """Return one skill/effect entity without accepting table or field names."""

        key = str(entity_key).strip()
        if not key:
            raise ValueError("entity_key 不能为空")
        if entity_kind == "gameplay_ability":
            return self.get_gameplay_ability(key)
        if entity_kind == "skill_damage":
            row = self.get_skill_damage(key)
            if row is not None:
                ability_id = row.get("ability_id")
                ability = None
                if ability_id not in (None, ""):
                    ability = self._one(
                        "SELECT 1 FROM gameplay_ability_catalog WHERE ability_id = ?",
                        (ability_id,),
                    )
                effect = self._one(
                    "SELECT 1 FROM gameplay_effect_catalog WHERE gameplay_effect_id = ?",
                    (row["damage_id"],),
                )
                row["ability_relation_status"] = (
                    "available" if ability is not None else "unavailable"
                )
                row["same_name_gameplay_effect_relation_status"] = (
                    "available" if effect is not None else "unavailable"
                )
            return row
        if entity_kind == "gameplay_effect":
            row = self.get_gameplay_effect(gameplay_effect_id=key)
            if row is not None:
                row["asset_path"] = str(row["class_path"]).split(".", 1)[0]
                row["tags"] = self._rows(
                    """
                    SELECT property_path, ordinal, tag_name
                    FROM combat_blueprint_tag
                    WHERE source_asset_path = ? ORDER BY property_path, ordinal
                    LIMIT ?
                    """,
                    (row["asset_path"], _MAX_PAGE_SIZE),
                )
            return row
        if entity_kind == "buff":
            return self.get_buff_definition(key)
        if entity_kind == "combat_effect":
            row = self._one(
                "SELECT * FROM combat_effect_definition WHERE effect_definition_id = ?",
                (key,),
            )
            if row is not None:
                row["parameters"] = _json_value(row.pop("parameters_json"), {})
                row["buff_links"] = self.list_combat_effect_buff_links(key)
            return row
        if entity_kind == "combat_curve":
            table_path, separator, curve_id = key.partition(chr(31))
            if not separator:
                raise ValueError("战斗曲线 key 无效")
            return self.get_combat_curve(table_path, curve_id)
        if entity_kind == "combat_level_curve":
            return self.get_combat_level_curve(key)
        if entity_kind == "reaction":
            return self._one(
                "SELECT * FROM reaction_definition WHERE reaction_type = ?", (key,)
            )
        if entity_kind == "combat_constant":
            return self._one(
                "SELECT * FROM combat_effect_constant WHERE constant_id = ?", (key,)
            )
        if entity_kind == "gameplay_tag":
            asset_path, separator, tag_name = key.partition(chr(31))
            if not separator:
                raise ValueError("Gameplay Tag key 无效")
            return self._one(
                """
                SELECT source_asset_path, property_path, ordinal, tag_name
                FROM combat_blueprint_tag
                WHERE source_asset_path = ? AND tag_name = ?
                ORDER BY property_path, ordinal LIMIT 1
                """,
                (asset_path, tag_name),
            )
        if entity_kind == "roguelike_modifier":
            row = self.get_roguelike_modifier(key)
            if row is not None:
                row["owner_resolution_status"] = "unavailable"
            return row
        raise ValueError(f"不支持的技能效果详情类型：{entity_kind!r}")

    def get_skill_damage_relation_coverage(self) -> dict[str, int]:
        """Count unresolved formal GA/GE targets without treating them as links."""

        return self._one(
            """
            SELECT
                SUM(CASE WHEN damage.ability_id IS NOT NULL
                              AND ability.ability_id IS NULL THEN 1 ELSE 0 END)
                    AS missing_ability_targets,
                SUM(CASE WHEN damage.ability_id IS NULL THEN 1 ELSE 0 END)
                    AS absent_ability_ids,
                SUM(CASE WHEN effect.gameplay_effect_id IS NULL THEN 1 ELSE 0 END)
                    AS missing_gameplay_effect_targets
            FROM skill_damage AS damage
            LEFT JOIN gameplay_ability_catalog AS ability
              ON ability.ability_id = damage.ability_id
            LEFT JOIN gameplay_effect_catalog AS effect
              ON effect.gameplay_effect_id = damage.damage_id
            """
        ) or {
            "missing_ability_targets": 0,
            "absent_ability_ids": 0,
            "missing_gameplay_effect_targets": 0,
        }

    def get_asset_catalog_detail(
        self,
        entity_kind: str,
        entity_key: str,
    ) -> dict[str, Any] | None:
        key = str(entity_key).strip()
        if not key:
            raise ValueError("entity_key 不能为空")
        if entity_kind == "blueprint":
            row = self._one(
                """
                SELECT asset_path, asset_name, asset_type, asset_kind,
                       character_id, source_file_id
                FROM combat_blueprint_asset WHERE asset_path = ?
                """,
                (key,),
            )
            if row is not None:
                row["relation_counts"] = {
                    "references": self._count_for(
                        "combat_blueprint_reference", "source_asset_path", key
                    ),
                    "tags": self._count_for("combat_blueprint_tag", "source_asset_path", key),
                    "properties": self._count_for(
                        "combat_blueprint_semantic_property", "source_asset_path", key
                    ),
                    "ability_effects": self._count_for(
                        "combat_ability_effect_binding", "ability_asset_path", key
                    ),
                    "ability_montages": self._count_for(
                        "combat_ability_montage_binding", "ability_asset_path", key
                    ),
                }
            return row
        if entity_kind == "montage":
            row = self._one("SELECT * FROM combat_montage WHERE asset_path = ?", (key,))
            if row is not None:
                row["sections"] = self._rows(
                    """
                    SELECT ordinal, section_name, next_section_name,
                           start_seconds, end_seconds, linked_animation_asset_path
                    FROM combat_montage_section
                    WHERE asset_path = ? ORDER BY ordinal
                    """,
                    (key,),
                )
                row["notify_count"] = self._count_for(
                    "combat_montage_notify", "asset_path", key
                )
            return row
        raise ValueError(f"不支持的资源详情类型：{entity_kind!r}")

    def _count_for(self, table: str, field: str, value: str) -> int:
        allowed = {
            ("combat_blueprint_reference", "source_asset_path"),
            ("combat_blueprint_tag", "source_asset_path"),
            ("combat_blueprint_semantic_property", "source_asset_path"),
            ("combat_montage_notify", "asset_path"),
            ("combat_ability_effect_binding", "ability_asset_path"),
            ("combat_ability_montage_binding", "ability_asset_path"),
        }
        if (table, field) not in allowed:
            raise ValueError("不支持的关系计数")
        row = self._one(f"SELECT COUNT(*) AS count FROM {table} WHERE {field} = ?", (value,))
        return int((row or {}).get("count", 0))

    def list_asset_relations(
        self,
        entity_kind: str,
        entity_key: str,
        relation_kind: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Page high-cardinality Blueprint and Montage evidence relations."""

        page_size, page_offset = self._pagination(limit, offset)
        specs = {
            ("blueprint", "references"): (
                "combat_blueprint_reference",
                "source_asset_path",
                "property_path, ordinal, relation_kind, target_asset_path, "
                "target_object_path, target_object_name, target_available, "
                "EXISTS(SELECT 1 FROM combat_blueprint_asset AS target "
                "WHERE target.asset_path = "
                "combat_blueprint_reference.target_asset_path) "
                "AS catalog_detail_available",
                "property_path, ordinal",
            ),
            ("blueprint", "tags"): (
                "combat_blueprint_tag",
                "source_asset_path",
                "source_asset_path, property_path, ordinal, tag_name",
                "property_path, ordinal",
            ),
            ("blueprint", "properties"): (
                "combat_blueprint_semantic_property",
                "source_asset_path",
                "property_path, ordinal, property_name, value_json",
                "property_path, ordinal",
            ),
            ("montage", "notifies"): (
                "combat_montage_notify",
                "asset_path",
                "ordinal, notify_name, notify_object_path, start_seconds, "
                "end_seconds, event_tag, track_index",
                "start_seconds, ordinal",
            ),
            ("blueprint", "ability_effects"): (
                "combat_ability_effect_binding",
                "ability_asset_path",
                "event_tag, ordinal, effect_asset_path, effect_id, target_type_asset_path, "
                "EXISTS(SELECT 1 FROM gameplay_effect_catalog AS target "
                "WHERE target.gameplay_effect_id = "
                "combat_ability_effect_binding.effect_id) AS target_available",
                "event_tag, ordinal",
            ),
            ("blueprint", "ability_montages"): (
                "combat_ability_montage_binding",
                "ability_asset_path",
                "ordinal, selector_key, montage_asset_path, montage_object_path, "
                "EXISTS(SELECT 1 FROM combat_montage AS target "
                "WHERE target.asset_path = "
                "combat_ability_montage_binding.montage_asset_path) AS target_available",
                "ordinal",
            ),
        }
        spec = specs.get((str(entity_kind), str(relation_kind)))
        if spec is None:
            raise ValueError("不支持的资源关系类型")
        table, field, columns, order_by = spec
        count = self._one(
            f"SELECT COUNT(*) AS count FROM {table} WHERE {field} = ?", (str(entity_key),)
        )
        rows = self._rows(
            f"SELECT {columns} FROM {table} WHERE {field} = ? "
            f"ORDER BY {order_by} LIMIT ? OFFSET ?",
            (str(entity_key), page_size, page_offset),
        )
        for row in rows:
            if "value_json" in row:
                row["value"] = _json_value(row.pop("value_json"), None)
            if "target_available" in row:
                row["target_available"] = bool(row["target_available"])
            if "catalog_detail_available" in row:
                row["catalog_detail_available"] = bool(
                    row["catalog_detail_available"]
                )
        return {
            "total": int((count or {}).get("count", 0)),
            "limit": page_size,
            "offset": page_offset,
            "items": rows,
        }


class StaticCatalogMiscDao(StaticCatalogMiscQueriesMixin, StaticGameDataDao):
    """Standalone facade for the B-domain integrator; the public DAO stays untouched."""
