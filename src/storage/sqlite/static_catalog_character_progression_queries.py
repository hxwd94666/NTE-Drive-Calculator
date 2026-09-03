# 查询角色养成静态库记录。
"""Read normalized character level and breakthrough progression rows."""

from __future__ import annotations

from typing import Any


class StaticCatalogCharacterProgressionQueriesMixin:
    def get_catalog_character_progression(
        self,
        character_id: int,
    ) -> dict[str, Any] | None:
        profile = self._one(
            """
            SELECT profile.character_id, profile.upgrade_pack_id,
                   profile.breakthrough_pack_id, profile.source_row_id,
                   source.row_key AS source_row_key,
                   source.content_sha256 AS source_content_sha256,
                   source.payload_json IS NOT NULL AS source_payload_available,
                   file.relative_path AS source_relative_path,
                   file.sha256 AS source_file_sha256
            FROM character_progression_profile AS profile
            JOIN source_row AS source ON source.source_row_id = profile.source_row_id
            JOIN source_file AS file ON file.source_file_id = source.source_file_id
            WHERE profile.character_id = ?
            """,
            (int(character_id),),
        )
        if profile is None:
            return None
        profile["upgrade_levels"] = tuple(self._rows(
            """
            SELECT level, need_exp, source.source_row_id,
                   source.row_key AS source_row_key,
                   source.content_sha256 AS source_content_sha256,
                   source.payload_json IS NOT NULL AS source_payload_available,
                   file.relative_path AS source_relative_path,
                   file.sha256 AS source_file_sha256
            FROM character_upgrade_level AS level_cost
            JOIN source_row AS source
              ON source.source_row_id = level_cost.source_row_id
            JOIN source_file AS file ON file.source_file_id = source.source_file_id
            WHERE level_cost.upgrade_pack_id = ?
            ORDER BY level
            """,
            (str(profile["upgrade_pack_id"]),),
        ))
        stages = self._rows(
            """
            SELECT stage.breakthrough_pack_id, stage.stage,
                   stage.max_character_level, stage.required_world_level,
                   stage.modify_pack_id, stage.source_row_id,
                   source.row_key AS source_row_key,
                   source.content_sha256 AS source_content_sha256,
                   source.payload_json IS NOT NULL AS source_payload_available,
                   file.relative_path AS source_relative_path,
                   file.sha256 AS source_file_sha256
            FROM character_breakthrough_stage AS stage
            JOIN source_row AS source ON source.source_row_id = stage.source_row_id
            JOIN source_file AS file ON file.source_file_id = source.source_file_id
            WHERE stage.breakthrough_pack_id = ?
            ORDER BY stage.stage
            """,
            (str(profile["breakthrough_pack_id"]),),
        )
        cost_rows = self._rows(
            """
            SELECT stage, ordinal, item_id, quantity
            FROM character_breakthrough_cost
            WHERE breakthrough_pack_id = ?
            ORDER BY stage, ordinal
            """,
            (str(profile["breakthrough_pack_id"]),),
        )
        costs_by_stage: dict[int, list[dict[str, Any]]] = {}
        for cost in cost_rows:
            costs_by_stage.setdefault(int(cost["stage"]), []).append(cost)
        for stage in stages:
            stage["costs"] = tuple(costs_by_stage.get(int(stage["stage"]), ()))
        profile["breakthrough_stages"] = tuple(stages)
        materials = self._rows(
            """
            SELECT material.item_id, material.experience_value,
                   material.source_row_id,
                   source.row_key AS source_row_key,
                   source.content_sha256 AS source_content_sha256,
                   source.payload_json IS NOT NULL AS source_payload_available,
                   file.relative_path AS source_relative_path,
                   file.sha256 AS source_file_sha256
            FROM character_exp_material AS material
            JOIN source_row AS source ON source.source_row_id = material.source_row_id
            JOIN source_file AS file ON file.source_file_id = source.source_file_id
            ORDER BY material.experience_value DESC, material.item_id
            """
        )
        material_costs = self._rows(
            """
            SELECT item_id, cost_item_id, quantity
            FROM character_exp_material_cost
            ORDER BY item_id, cost_item_id
            """
        )
        costs_by_material: dict[str, list[dict[str, Any]]] = {}
        for cost in material_costs:
            costs_by_material.setdefault(str(cost["item_id"]), []).append(cost)
        for material in materials:
            material["costs"] = tuple(
                costs_by_material.get(str(material["item_id"]), ())
            )
        profile["exp_materials"] = tuple(materials)
        return profile


__all__ = ["StaticCatalogCharacterProgressionQueriesMixin"]
