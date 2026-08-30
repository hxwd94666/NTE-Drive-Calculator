# 使用冻结配装、修改养成和手工边际属性重建战报角色面板。
"""Battle-build stat reconstruction kept outside the history orchestrator."""

from __future__ import annotations

from typing import Any

from src.services.battle_build_equipment_service import battle_equipment_items
from src.services.character_shape_bonus_service import (
    character_shape_profile_fields,
)
from src.services.battle_report_persistence_service import (
    BattleReportPersistenceDependencies,
)
from src.services.official_role_attribute_service import (
    calculate_official_role_combat_stat_components,
    calculate_official_role_combat_stat_sources,
)
from src.services.official_role_page_service import load_official_role_detail


class BattleBuildStatReconstructionService:
    """Rebuild missing or edited role stats without mutating saved battle facts."""

    @staticmethod
    def enrich(
        build: dict[str, Any],
        dependencies: BattleReportPersistenceDependencies,
    ) -> None:
        if dependencies.static_database_path is None:
            return
        for character in build.get("characters") or ():
            existing_stats = list(character.get("stats") or ())
            existing_sources = {
                str(row.get("source_group") or "") for row in existing_stats
            }
            world_bonus_repair_required = "world_bonus" not in existing_sources
            character_id = int(character["character_id"])
            items = battle_equipment_items(character)
            try:
                detail = load_official_role_detail(
                    dependencies.user_database_path,
                    character_id,
                    include_inventory_contexts=False,
                    static_database_path=dependencies.static_database_path,
                )
                frozen_world_bonus = {
                    str(row.get("property_id") or ""): float(
                        row.get("value") or 0.0
                    )
                    for row in existing_stats
                    if str(row.get("source_group") or "") == "world_bonus"
                }
                if frozen_world_bonus:
                    detail["world_bonus"] = {
                        "yaodao_attack_add": frozen_world_bonus.get(
                            "AtkAdd", 0.0
                        ),
                        "quantao_crit_damage": frozen_world_bonus.get(
                            "CritDamageBase", 0.0
                        ),
                    }
                profile = dict(character.get("profile") or {})
                static_shape_fields = character_shape_profile_fields(
                    detail.get("shape_bonus")
                )
                shape_repair_required = any(
                    profile.get(key) != value
                    for key, value in static_shape_fields.items()
                )
                if (
                    {"character", "fork", "equipment"}.issubset(existing_sources)
                    and not shape_repair_required
                    and not world_bonus_repair_required
                ):
                    continue
                profile.update(static_shape_fields)
                character["profile"] = profile
                detail["profile"] = profile
                sources = calculate_official_role_combat_stat_sources(detail, items)
                resolved = calculate_official_role_combat_stat_components(detail, items)
            except (OSError, RuntimeError, ValueError):
                continue
            overrides = {
                str(key): float(value)
                for key, value in (
                    (character.get("profile") or {}).get("battle_stat_overrides")
                    or {}
                ).items()
            }
            resolved_values = {row.key: float(row.value) for row in resolved}
            source_rows = [
                {
                    "source_group": source_group,
                    "property_id": row.key,
                    "display_name": row.label,
                    "value": row.value,
                    "is_percent": row.percent,
                    "ordinal": ordinal,
                }
                for source_group, rows in sources.items()
                for ordinal, row in enumerate(rows)
            ]
            source_rows.extend(
                {
                    "source_group": "battle_override",
                    "property_id": property_id,
                    "display_name": next(
                        (row.label for row in resolved if row.key == property_id),
                        property_id,
                    ),
                    "value": value - resolved_values.get(property_id, 0.0),
                    "is_percent": next(
                        (row.percent for row in resolved if row.key == property_id),
                        False,
                    ),
                    "ordinal": ordinal,
                }
                for ordinal, (property_id, value) in enumerate(overrides.items())
                if property_id in resolved_values
                and value != resolved_values[property_id]
            )
            rebuild_resolved = (
                shape_repair_required
                or world_bonus_repair_required
                or "resolved" not in existing_sources
            )
            if rebuild_resolved:
                source_rows.extend(
                    {
                        "source_group": "resolved",
                        "property_id": row.key,
                        "display_name": row.label,
                        "value": overrides.get(row.key, row.value),
                        "is_percent": row.percent,
                        "ordinal": ordinal,
                    }
                    for ordinal, row in enumerate(resolved)
                )
            character["stats"] = [
                row
                for row in existing_stats
                if (
                    str(row.get("source_group") or "") == "resolved"
                    and not rebuild_resolved
                )
            ] + source_rows
            edited_equipment = bool(
                character.pop("_edited_equipment_active", False)
            )
            edited_snapshot = bool(character.pop("_edited_snapshot_active", False))
            character["stat_snapshot_source"] = (
                "user_edited_equipment_reconstructed"
                if edited_equipment
                else "user_edited_reconstructed"
                if edited_snapshot
                else "reconstructed_current_static_shape"
                if shape_repair_required
                else "reconstructed_current_world_bonus"
                if world_bonus_repair_required
                else "reconstructed_current_static"
            )
