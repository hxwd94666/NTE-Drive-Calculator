# 按旧配装口径统一计算新页面使用的官方 ID 空幕与驱动加成。
"""Calculate official-ID equipment bonuses with the established allocation semantics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.domain.equipment_normalizer import calculate_drive_main_stats


@dataclass(frozen=True, slots=True)
class OfficialEquipmentStatTotal:
    property_id: str
    value: float
    percent: bool


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _stat_value(stat: Any, key: str, default: Any = None) -> Any:
    if isinstance(stat, Mapping):
        return stat.get(key, default)
    return getattr(stat, key, default)


def _grid_count(item: Any) -> int:
    direct = _value(item, "grid_count", None)
    if direct is None:
        direct = _value(item, "grid", None)
    if direct is not None:
        try:
            return int(direct)
        except (TypeError, ValueError):
            pass
    geometry = str(_value(item, "geometry", "") or _value(item, "shape_id", "") or "")
    numbers = re.findall(r"\d+", geometry)
    return int(numbers[-1]) if numbers else 0


def _extra_shape_grid_count(label: str) -> int | None:
    numbers = re.findall(r"\d+", str(label or ""))
    return int(numbers[-1]) if numbers else None


def _legacy_quality(item: Any) -> str:
    quality = str(_value(item, "quality", "Gold") or "Gold")
    return {
        "orange": "Gold",
        "purple": "Purple",
        "blue": "Blue",
    }.get(quality.casefold(), quality)


def _extra_buffs(source: Mapping[str, float] | Iterable[tuple[str, float]]) -> tuple[tuple[str, float], ...]:
    rows = source.items() if isinstance(source, Mapping) else source
    return tuple((str(property_id), float(value)) for property_id, value in rows)


def calculate_official_equipment_stats(
    items: Iterable[Any],
    *,
    extra_shape_label: str = "",
    extra_shape_buffs: Mapping[str, float] | Iterable[tuple[str, float]] = (),
    property_percent: Mapping[str, bool] | None = None,
) -> tuple[OfficialEquipmentStatTotal, ...]:
    """Return core and module totals using the old equipment-page calculation rules."""

    totals: dict[str, float] = {}
    percents = {str(key): bool(value) for key, value in (property_percent or {}).items()}
    modules: list[Any] = []

    def add(property_id: Any, value: Any, percent: bool) -> None:
        key = str(property_id or "")
        number = float(value or 0.0)
        if not key or not number:
            return
        totals[key] = totals.get(key, 0.0) + number
        percents[key] = percents.get(key, False) or bool(percent)

    for item in items:
        kind = str(_value(item, "kind", "") or "")
        main_stats = tuple(_value(item, "main_stats", ()) or ())
        sub_stats = tuple(_value(item, "sub_stats", ()) or ())
        if kind == "module":
            grid_count = _grid_count(item)
            if grid_count <= 0:
                continue
            modules.append(item)
            intrinsic = calculate_drive_main_stats(grid_count, _legacy_quality(item))
            add("AtkAdd", intrinsic["攻击力"], False)
            add("HPMaxAdd", intrinsic["生命值"], False)
            stats = sub_stats
        else:
            stats = (*main_stats, *sub_stats)
        for stat in stats:
            add(
                _stat_value(stat, "property_id", ""),
                _stat_value(stat, "value", 0.0),
                bool(_stat_value(stat, "percent", False)),
            )

    target_grid_count = _extra_shape_grid_count(extra_shape_label)
    if target_grid_count is not None:
        matched = sum(_grid_count(item) == target_grid_count for item in modules)
        if matched:
            for property_id, display_value in _extra_buffs(extra_shape_buffs):
                is_percent = percents.get(property_id, False)
                normalized_value = display_value / 100.0 if is_percent else display_value
                add(property_id, normalized_value * matched, is_percent)

    return tuple(
        OfficialEquipmentStatTotal(property_id, value, percents.get(property_id, False))
        for property_id, value in totals.items()
        if value
    )
