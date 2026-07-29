# 将官方角色装备投影为统一基础权重评分。
"""Qt-free scoring adapter for official-role equipment dictionaries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.optimizer.scoring import ScoringEngine
from src.services.equipment_scoring_service import score_drive_stats, score_tape_stats
from src.services.sqlite_allocation_inventory import (
    AllocationInventoryProjectionError,
    legacy_shape_id,
)


def _attribute_name(detail: Mapping[str, Any], property_id: str) -> str:
    attribute = (detail.get("attributes") or {}).get(property_id) or {}
    return str(
        attribute.get("filter_name_zh")
        or attribute.get("display_name_zh")
        or property_id
    )


def score_official_role_equipment(
    engine: ScoringEngine | None,
    *,
    detail: Mapping[str, Any],
    item: Mapping[str, Any],
    shape_areas: Mapping[str, int],
) -> float:
    """Score one projected official-role item without importing a page."""

    if engine is None:
        return 0.0
    weights = {
        _attribute_name(detail, str(property_id)): float(weight)
        for property_id, weight in (detail.get("property_weights") or {}).items()
    }
    main_weights = {
        _attribute_name(detail, str(property_id)): float(weight)
        for property_id, weight in (
            detail.get("main_property_weights") or {}
        ).items()
    }
    sub_stat_names = tuple(
        _attribute_name(detail, str(stat.get("property_id") or ""))
        for stat in item.get("sub_stats") or ()
    )
    quality = {
        "orange": "Gold",
        "gold": "Gold",
        "purple": "Purple",
        "blue": "Blue",
    }.get(str(item.get("quality") or "").casefold(), "Gold")
    if str(item.get("kind") or "") == "core":
        main_stat = next(
            (
                _attribute_name(detail, str(stat.get("property_id") or ""))
                for stat in item.get("main_stats") or ()
            ),
            "",
        )
        return score_tape_stats(
            engine,
            main_stat_name=main_stat,
            sub_stat_names=sub_stat_names,
            weights=weights,
            quality=quality,
            main_weights=main_weights,
        )
    geometry = str(item.get("geometry") or "")
    try:
        shape_id = legacy_shape_id(geometry)
    except AllocationInventoryProjectionError:
        shape_id = geometry
    area = int(item.get("grid_count") or shape_areas.get(shape_id, 3))
    return score_drive_stats(
        engine,
        sub_stat_names=sub_stat_names,
        area=area,
        weights=weights,
        quality=quality,
    )
