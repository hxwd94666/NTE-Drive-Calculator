# 兼容已发布 v31 数据集的弧盘常驻属性只读投影。
"""Read-only v31 projection for fork unconditional panel properties."""

from __future__ import annotations

from typing import Any

from .fork_permanent_projection import (
    FORK_PERMANENT_EVIDENCE_SQL,
    FORK_REFINEMENT_LEVEL_SQL,
    resolve_projection_rows,
)

from .protocols import StaticDataDaoMixinHost


class ForkPermanentPropertyProjectionMixin(StaticDataDaoMixinHost):
    def _legacy_fork_permanent_properties(self) -> dict[str, list[dict[str, Any]]]:
        """Derive v31 bonuses from its normalized curve and Modifier facts."""

        required_tables = (
            "fork_item", "fork_star_level", "fork_star_parameter",
            "fork_refinement_parameter_value", "combat_effect_definition",
            "combat_effect_buff_link", "combat_blueprint_reference",
            "buff_definition", "buff_modifier",
        )
        for table_name in required_tables:
            if self._one(
                "SELECT 1 AS found FROM sqlite_master "
                "WHERE type = 'table' AND name = ?",
                (table_name,),
            ) is None:
                return {}
        resolved, _audit = resolve_projection_rows(
            self._rows(FORK_PERMANENT_EVIDENCE_SQL),
            self._rows(FORK_REFINEMENT_LEVEL_SQL),
        )
        by_fork: dict[str, list[dict[str, Any]]] = {}
        for value in resolved:
            by_fork.setdefault(value.fork_id, []).append({
                "refinement_level": value.refinement_level,
                "property_id": value.property_id,
                "source_parameter_name_id": value.parameter_name_id,
                "property_value": value.property_value,
                "modifier_operation": value.modifier_operation,
                "source_calculation_asset_path": value.calculation_asset_path,
                "source_effect_definition_id": value.effect_definition_id,
                "source_row_id": value.source_row_id,
            })
        return by_fork
