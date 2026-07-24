# 统一毕业模板在构建期与角色页运行时的额外形状加成口径。
"""Normalize extra-shape bonuses used by graduation equipment templates."""

from __future__ import annotations

from collections.abc import Mapping
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
