# 计算角色面板属性并汇总装备加成。
"""Shared, side-effect-free character panel stat calculation.

This module calculates the attributes shown on a character panel.  It does
not include conditional weapon skills or set effects; those belong to a future
combat layer rather than a static panel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


StatTotals = dict[str, float]

_PANEL_SYNTHESIS = (
    ("攻击力白值", "攻击力%", "攻击力", "总攻击力"),
    ("生命白值", "生命值%", "生命值", "总生命值"),
    ("防御力白值", "防御力%", "防御力", "总防御力"),
)


@dataclass(frozen=True)
class CharacterStatContext:
    """All dependencies of a panel calculation, supplied explicitly."""

    role_models: dict[str, Any]
    roles_db: dict[str, Any]
    weapons_db: dict[str, Any]
    shape_areas: dict[str, Any]
    stats_config: dict[str, Any]
    stat_alias_mapping: dict[str, str]
    custom_weapons: dict[str, str]


@dataclass(frozen=True)
class CharacterPanel:
    totals: StatTotals
    source_totals: dict[str, StatTotals]
    weapon_name: str
    role_level: int | None


def item_value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def stat_number_value(value: Any) -> float:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def quality_coef(quality: Any) -> float:
    return {"Gold": 1.0, "Purple": 0.8, "Blue": 0.6}.get(str(quality or "Gold"), 1.0)


def canonical_stat_name(stat: Any, aliases: dict[str, str]) -> str:
    name = str(stat or "").strip()
    return aliases.get(name, name) if name else ""


def add_stat_total(totals: StatTotals, stat: Any, value: Any, aliases: dict[str, str]) -> None:
    name = canonical_stat_name(stat, aliases)
    number = stat_number_value(value)
    if name and number:
        totals[name] = round(totals.get(name, 0.0) + number, 4)


def add_stat_map(totals: StatTotals, stats: Any, aliases: dict[str, str]) -> None:
    for stat, value in (stats or {}).items():
        add_stat_total(totals, stat, value, aliases)


def fallback_tape_main_value(main_stat: Any, quality: Any, stats_config: dict[str, Any], aliases: dict[str, str]) -> float:
    configured = (stats_config or {}).get("tape_main_stat_values", {})
    raw_name = str(main_stat or "").strip()
    name = canonical_stat_name(raw_name, aliases)
    coef = quality_coef(quality)
    if raw_name in configured:
        return stat_number_value(configured[raw_name]) * coef
    if name in configured:
        return stat_number_value(configured[name]) * coef
    if name == "暴击伤害%":
        return 60.0 * coef
    if name == "暴击率":
        return 30.0 * coef
    if name in {"攻击力%", "防御力%", "生命值%"}:
        return 37.5 * coef
    if name in {"环合强度", "倾陷强度"}:
        return 180.0 * coef
    if "治疗加成" in name:
        return 34.5 * coef
    if "伤害增强" in name:
        return 37.5 * coef
    return 0.0


def drive_area(drive: Any, shape_areas: dict[str, Any]) -> int:
    direct = item_value(drive, "area", None) or item_value(drive, "score_area", None)
    if direct is not None:
        try:
            return int(direct)
        except (TypeError, ValueError):
            pass
    shape_id = str(item_value(drive, "shape_id", "") or "")
    mapped = (shape_areas or {}).get(shape_id)
    if mapped is not None:
        try:
            return int(mapped)
        except (TypeError, ValueError):
            pass
    match = re.search(r"_(\d+)", shape_id)
    return int(match.group(1)) if match else 0


def shape_intrinsic_stats(drive: Any, shape_areas: dict[str, Any]) -> StatTotals:
    area = drive_area(drive, shape_areas)
    return {"攻击力": float(area * 21), "生命值": float(area * 280)} if area > 0 else {}


def _highest_available_level(level_stats: Any) -> int | None:
    numeric = []
    for level in (level_stats or {}).keys():
        try:
            numeric.append(int(level))
        except (TypeError, ValueError):
            continue
    return max(numeric) if numeric else None


def _stats_at_level(template: dict[str, Any], level: int | None) -> StatTotals:
    level_stats = template.get("level_sub_stats", {}) if isinstance(template, dict) else {}
    if not isinstance(level_stats, dict):
        return {}
    chosen = level if level is not None else _highest_available_level(level_stats)
    stats = level_stats.get(str(chosen), {}) if chosen is not None else {}
    return dict(stats) if isinstance(stats, dict) else {}


def _model_role_panel_stats(model_role: dict[str, Any]) -> tuple[StatTotals, int | None]:
    level = _highest_available_level(model_role.get("level_sub_stats", {}))
    stats = dict(model_role.get("sub_stats", {}) or {})
    # The level table is authoritative for level-dependent base panel stats;
    # sub_stats retains fixed panel entries such as initial crit values.
    stats.update(_stats_at_level(model_role, level))
    return stats, level


def _weapon_stats(weapon: dict[str, Any]) -> StatTotals:
    if not isinstance(weapon, dict):
        return {}
    stats = dict(weapon.get("sub_stats", {}) or {})
    if stats:
        return stats
    try:
        level = int(weapon.get("level"))
    except (TypeError, ValueError):
        level = None
    return _stats_at_level(weapon, level)


def resolve_panel_weapon(ctx: CharacterStatContext, role_name: str, model_role: dict[str, Any]) -> tuple[str, StatTotals]:
    custom_name = str((ctx.custom_weapons or {}).get(role_name, "") or "").strip()
    if custom_name:
        weapon = (ctx.weapons_db or {}).get(custom_name, {})
        return custom_name, _weapon_stats(weapon) if isinstance(weapon, dict) else {}
    weapon = model_role.get("weapon", {}) if isinstance(model_role, dict) else {}
    name = str(weapon.get("name", "") or "") if isinstance(weapon, dict) else ""
    return name, _weapon_stats(weapon)


def _extra_shape_target(role_data: dict[str, Any]) -> int | None:
    match = re.search(r"(\d+)", str(role_data.get("extra_shape_label", "") or ""))
    return int(match.group(1)) if match else None


def allocation_equipment_stats(ctx: CharacterStatContext, role_name: str, tape: Any, drives: list[Any] | None) -> StatTotals:
    """Return tape + drive contribution, including drive-shape fixed stats."""
    totals: StatTotals = {}
    if tape:
        main_stat = item_value(tape, "main_stats", "")
        main_value = item_value(tape, "main_value", None)
        if main_value is None:
            main_value = fallback_tape_main_value(main_stat, item_value(tape, "quality", "Gold"), ctx.stats_config, ctx.stat_alias_mapping)
        add_stat_total(totals, main_stat, main_value, ctx.stat_alias_mapping)
        add_stat_map(totals, item_value(tape, "sub_stats", {}), ctx.stat_alias_mapping)
    drives = list(drives or [])
    for drive in drives:
        add_stat_map(totals, item_value(drive, "sub_stats", {}), ctx.stat_alias_mapping)
        add_stat_map(totals, shape_intrinsic_stats(drive, ctx.shape_areas), ctx.stat_alias_mapping)
    role_data = (ctx.roles_db or {}).get(role_name, {})
    target_area = _extra_shape_target(role_data) if isinstance(role_data, dict) else None
    if target_area:
        matched = sum(drive_area(drive, ctx.shape_areas) == target_area for drive in drives)
        for stat, value in (role_data.get("extra_shape_buffs", {}) or {}).items():
            add_stat_total(totals, stat, stat_number_value(value) * matched, ctx.stat_alias_mapping)
    return totals


def synthesize_panel_totals(raw_totals: StatTotals) -> StatTotals:
    totals = dict(raw_totals)
    for base_key, pct_key, flat_key, display_key in _PANEL_SYNTHESIS:
        base = totals.pop(base_key, 0.0)
        pct = totals.pop(pct_key, 0.0)
        flat = totals.pop(flat_key, 0.0)
        if base or pct or flat:
            totals[display_key] = round(base * (1.0 + pct / 100.0) + flat, 4)
    return {stat: value for stat, value in totals.items() if value}


def build_character_panel(ctx: CharacterStatContext, role_name: str, tape: Any = None, drives: list[Any] | None = None) -> CharacterPanel:
    """Build visible panel attributes from model role + weapon + allocation."""
    model_role = (ctx.role_models or {}).get(role_name, {})
    model_role = model_role if isinstance(model_role, dict) else {}
    role_stats, role_level = _model_role_panel_stats(model_role)
    weapon_name, weapon_stats = resolve_panel_weapon(ctx, role_name, model_role)
    equipment_stats = allocation_equipment_stats(ctx, role_name, tape, drives)
    raw_totals: StatTotals = {}
    for source in (role_stats, weapon_stats, equipment_stats):
        add_stat_map(raw_totals, source, ctx.stat_alias_mapping)
    return CharacterPanel(
        totals=synthesize_panel_totals(raw_totals),
        source_totals={"role_panel": role_stats, "weapon_panel": weapon_stats, "allocation_equipment": equipment_stats},
        weapon_name=weapon_name,
        role_level=role_level,
    )
