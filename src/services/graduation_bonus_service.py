# 统一毕业模板在构建期与角色页运行时的额外形状加成口径。
"""Normalize extra-shape bonuses used by graduation equipment templates."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


def graduation_extra_shape_stats(
    shape_bonus: Mapping[str, Any] | None,
    extra_shape_count: int,
    attributes: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the full graduation bonus for all matching blueprint modules.

    Shape-bonus display values are expressed as whole percentages in SQLite
    (for example ``9`` means 9%).  Direct-damage inputs use fractions, so the
    conversion belongs here and must never be duplicated by the caller.
    """

    count = max(0, int(extra_shape_count or 0))
    if count <= 0 or not isinstance(shape_bonus, Mapping):
        return []
    attribute_rows = attributes or {}
    rows: list[dict[str, Any]] = []
    for raw in shape_bonus.get("properties") or ():
        if not isinstance(raw, Mapping):
            continue
        property_id = str(raw.get("property_id") or "")
        if not property_id:
            continue
        try:
            display_value = float(raw.get("display_value") or 0.0)
        except (TypeError, ValueError):
            continue
        if not display_value:
            continue
        percent = bool(
            raw.get("show_percent")
            or (attribute_rows.get(property_id) or {}).get("show_percent")
        )
        value = display_value * count
        rows.append({
            "property_id": property_id,
            "value": value / 100.0 if percent else value,
            "percent": percent,
        })
    return rows


def graduation_extra_shape_drive_count(
    shape_bonus: Mapping[str, Any] | None,
    equipment_plan: Mapping[str, Any] | None,
    equipment_by_id: Mapping[str, Mapping[str, Any]],
) -> int:
    """Count modules matching the effective shared shape rule in one blueprint."""

    if not isinstance(shape_bonus, Mapping) or not isinstance(equipment_plan, Mapping):
        return 0
    target_grid_count = int(shape_bonus.get("shape_grid_count") or 0)
    if target_grid_count <= 0:
        numbers = re.findall(r"\d+", str(shape_bonus.get("shape_label") or ""))
        target_grid_count = int(numbers[-1]) if numbers else 0
    if target_grid_count <= 0:
        return 0
    return sum(
        int((equipment_by_id.get(str(item_id)) or {}).get("grid_count") or 0)
        == target_grid_count
        for item_id in equipment_plan.get("module_item_ids") or ()
    )
