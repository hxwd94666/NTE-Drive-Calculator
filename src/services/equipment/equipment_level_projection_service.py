# 将固定背包快照中的装备投影为官方满级展示与计算数据。
"""Project existing equipment rolls onto official max-level main-stat curves."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


def _curve_id(
    property_id: str,
    *,
    kind: str,
    quality: str,
    grid_count: int,
) -> str | None:
    quality_id = str(quality or "").strip().upper()
    if not property_id or quality_id not in {"BLUE", "PURPLE", "ORANGE"}:
        return None
    if kind == "core":
        category = "Core"
    elif kind == "module" and grid_count > 0:
        category = str(grid_count)
    else:
        return None
    return f"{property_id}_{category}_ITEM_QUALITY_{quality_id}"


def project_equipment_items_to_max_level(
    items: Iterable[Mapping[str, Any]],
    static_dao: StaticGameDataDao,
) -> list[dict[str, Any]]:
    """Keep rolled substats, but evaluate every existing main stat at max level."""

    templates = {
        str(template["item_id"]): template
        for template in static_dao.list_equipment_items()
    }
    projected: list[dict[str, Any]] = []
    for source in items:
        item = dict(source)
        template = templates.get(str(item.get("item_id") or ""), {})
        kind = str(item.get("kind") or template.get("kind") or "")
        quality = str(item.get("quality") or template.get("quality") or "")
        grid_count = int(item.get("grid_count") or template.get("grid_count") or 0)
        max_level = int(
            template.get("max_level")
            or item.get("max_level")
            or item.get("level")
            or 0
        )
        main_stats = []
        for raw_stat in item.get("main_stats") or ():
            stat = dict(raw_stat)
            curve_id = _curve_id(
                str(stat.get("property_id") or ""),
                kind=kind,
                quality=quality,
                grid_count=grid_count,
            )
            value = (
                static_dao.evaluate_equipment_base_attribute_curve(
                    curve_id,
                    max_level,
                )
                if curve_id is not None
                else None
            )
            if value is not None:
                stat["value"] = float(value)
            main_stats.append(stat)
        item.update(
            {
                "kind": kind,
                "quality": quality.casefold(),
                "grid_count": grid_count or item.get("grid_count"),
                "level": max_level,
                "max_level": max_level,
                "main_stats": main_stats,
                "sub_stats": [
                    dict(stat) for stat in item.get("sub_stats") or ()
                ],
            }
        )
        projected.append(item)
    return projected
