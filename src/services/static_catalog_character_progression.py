"""Project normalized character progression rows into immutable DTOs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.services.static_catalog_character_models import (
    CatalogSource,
    CharacterBreakthroughRequirement,
    CharacterExperienceMaterial,
    CharacterMaterialCost,
    CharacterProgressionProfile,
    CharacterUpgradeLevel,
)


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _source(row: Mapping[str, Any], table: str) -> CatalogSource:
    return CatalogSource(
        table_name=table,
        row_id=_optional_int(row.get("source_row_id")),
        row_key=_optional_text(row.get("source_row_key")),
        relative_path=_optional_text(row.get("source_relative_path")),
        content_sha256=_optional_text(row.get("source_content_sha256")),
        file_sha256=_optional_text(row.get("source_file_sha256")),
        payload_available=bool(row.get("source_payload_available", False)),
    )


def project_character_progression(
    row: Mapping[str, Any],
) -> CharacterProgressionProfile:
    return CharacterProgressionProfile(
        character_id=int(row["character_id"]),
        upgrade_pack_id=str(row["upgrade_pack_id"]),
        breakthrough_pack_id=str(row["breakthrough_pack_id"]),
        upgrade_levels=tuple(
            CharacterUpgradeLevel(
                level=int(item["level"]),
                need_exp=int(item["need_exp"]),
                source=_source(item, "character_upgrade_level"),
            )
            for item in row.get("upgrade_levels", ())
        ),
        breakthrough_stages=tuple(
            CharacterBreakthroughRequirement(
                stage=int(item["stage"]),
                max_character_level=int(item["max_character_level"]),
                required_world_level=int(item["required_world_level"]),
                costs=tuple(
                    CharacterMaterialCost(
                        item_id=str(cost["item_id"]),
                        quantity=int(cost["quantity"]),
                    )
                    for cost in item.get("costs", ())
                ),
                source=_source(item, "character_breakthrough_stage"),
            )
            for item in row.get("breakthrough_stages", ())
        ),
        experience_materials=tuple(
            CharacterExperienceMaterial(
                item_id=str(item["item_id"]),
                experience_value=int(item["experience_value"]),
                costs=tuple(
                    CharacterMaterialCost(
                        item_id=str(cost["cost_item_id"]),
                        quantity=int(cost["quantity"]),
                    )
                    for cost in item.get("costs", ())
                ),
                source=_source(item, "character_exp_material"),
            )
            for item in row.get("exp_materials", ())
        ),
        source=_source(row, "character_progression_profile"),
    )


__all__ = ["project_character_progression"]
