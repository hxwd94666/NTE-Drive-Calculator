# 提供构建期与只读兼容层共用的弧盘常驻属性 SQL 投影。
"""Shared SQL projection for unconditional fork panel properties."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.domain.fork_permanent_property import (
    ForkPermanentAudit,
    ForkPermanentProperty,
    resolve_fork_permanent_properties,
)


FORK_PERMANENT_EVIDENCE_SQL = """
    WITH RECURSIVE modifier_ancestry(
        effect_definition_id, linked_asset_path,
        modifier_asset_path, inheritance_depth, visited
    ) AS (
        SELECT l.effect_definition_id, l.target_asset_path,
               l.target_asset_path, 0,
               '|' || lower(l.target_asset_path) || '|'
        FROM combat_effect_buff_link AS l
        UNION ALL
        SELECT a.effect_definition_id, a.linked_asset_path,
               r.target_asset_path, a.inheritance_depth + 1,
               a.visited || lower(r.target_asset_path) || '|'
        FROM modifier_ancestry AS a
        JOIN combat_blueprint_reference AS r
          ON r.source_asset_path = a.modifier_asset_path
         AND r.property_path LIKE '%.Super'
         AND r.target_available = 1
        JOIN buff_definition AS parent
          ON parent.asset_path = r.target_asset_path
        WHERE a.inheritance_depth < 8
          AND instr(a.visited, '|' || lower(r.target_asset_path) || '|') = 0
    ), nearest_modifier_asset AS (
        SELECT a.effect_definition_id, a.linked_asset_path,
               a.modifier_asset_path, a.inheritance_depth
        FROM modifier_ancestry AS a
        WHERE EXISTS (
            SELECT 1 FROM buff_modifier AS candidate
            WHERE candidate.asset_path = a.modifier_asset_path
        )
          AND a.inheritance_depth = (
              SELECT MIN(other.inheritance_depth)
              FROM modifier_ancestry AS other
              WHERE other.effect_definition_id = a.effect_definition_id
                AND other.linked_asset_path = a.linked_asset_path
                AND EXISTS (
                    SELECT 1 FROM buff_modifier AS candidate
                    WHERE candidate.asset_path = other.modifier_asset_path
                )
          )
    )
    SELECT f.fork_id, s.star_level, p.ordinal AS parameter_ordinal,
           p.name_id, v.value,
           v.source_row_id, m.property_id, m.modifier_operation,
           m.calculation_asset_path, e.effect_definition_id,
           a.linked_asset_path, a.modifier_asset_path,
           a.inheritance_depth,
           m.application_requirement_asset_path,
           m.source_require_tags_json, m.source_ignore_tags_json,
           m.target_require_tags_json, m.target_ignore_tags_json
    FROM fork_item AS f
    JOIN fork_star_level AS s ON s.star_pack_id = f.star_pack_id
    JOIN fork_star_parameter AS p
      ON p.star_pack_id = s.star_pack_id
     AND p.star_level = s.star_level
    JOIN fork_refinement_parameter_value AS v
      ON v.name_id = p.name_id
     AND v.refinement_level = s.star_level
    JOIN combat_effect_definition AS e
      ON e.effect_definition_id =
         'fork_star:' || s.star_pack_id || ':' || s.star_level
    JOIN nearest_modifier_asset AS a
      ON a.effect_definition_id = e.effect_definition_id
    JOIN buff_modifier AS m ON m.asset_path = a.modifier_asset_path
    WHERE m.property_id IS NOT NULL
      AND m.modifier_operation = 'EGameplayModOp::Additive'
      AND m.calculation_asset_path IS NOT NULL
    ORDER BY f.fork_id, s.star_level, p.ordinal, m.ordinal
"""

FORK_REFINEMENT_LEVEL_SQL = """
    SELECT f.fork_id, s.star_level
    FROM fork_item AS f
    JOIN fork_star_level AS s ON s.star_pack_id = f.star_pack_id
    ORDER BY f.fork_id, s.star_level
"""


def expected_level_map(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, set[int]]:
    expected: dict[str, set[int]] = {}
    for row in rows:
        expected.setdefault(str(row["fork_id"]), set()).add(int(row["star_level"]))
    return expected


def resolve_projection_rows(
    evidence_rows: Iterable[Mapping[str, Any]],
    level_rows: Iterable[Mapping[str, Any]],
) -> tuple[tuple[ForkPermanentProperty, ...], tuple[ForkPermanentAudit, ...]]:
    return resolve_fork_permanent_properties(
        evidence_rows,
        expected_level_map(level_rows),
    )


def cursor_dicts(cursor: Any) -> list[dict[str, Any]]:
    columns = tuple(str(column[0]) for column in cursor.description)
    return [dict(zip(columns, row, strict=True)) for row in cursor]
