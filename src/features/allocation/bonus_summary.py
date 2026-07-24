# 汇总分配结果中的装备与角色面板属性。
"""Allocation-result stat summaries backed by the shared panel stat engine."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.features.allocation.plan_diff_pairing import drive_item_area
from src.features.role.stat_engine import (
    CharacterStatContext,
    add_stat_total as _engine_add_stat_total,
    allocation_equipment_stats,
    build_character_panel,
    canonical_stat_name as _engine_canonical_stat_name,
    fallback_tape_main_value as _engine_fallback_tape_main_value,
    item_value as _engine_item_value,
    quality_coef as _engine_quality_coef,
    stat_number_value as _engine_stat_number_value,
)
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SHAPE_ID,
    EQUIP_TYPE,
    EQUIP_UID,
    PLAN_CUSTOM_WEAPON,
    ROLE_LAST_DIFF,
)


BonusRows = list[tuple[str, float]]
AlignedBonusRow = dict[str, Any]

_CHARACTER_STAT_SYNTHESIS = (
    ("攻击力白值", "攻击力%", "攻击力", "总攻击力", "小攻击"),
    ("生命白值", "生命值%", "生命值", "总生命值", "小生命"),
    ("防御力白值", "防御力%", "防御力", "总防御力", "小防御"),
)
_CHARACTER_STAT_ALIASES = {
    "总攻击力": "攻击力白值", "攻击力白值": "总攻击力", "小攻击": "攻击力", "攻击力": "小攻击",
    "总生命值": "生命白值", "生命白值": "总生命值", "小生命": "生命值", "生命值": "小生命",
    "总防御力": "防御力白值", "防御力白值": "总防御力", "小防御": "防御力", "防御力": "小防御",
}


@dataclass(frozen=True)
class BonusSummaryContext:
    roles_db: dict[str, Any]
    shape_areas: dict[str, Any]
    stats_config: dict[str, Any]
    stat_alias_mapping: dict[str, str]
    weapons_db: dict[str, Any] = field(default_factory=dict)
    custom_weapons: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_window(cls, window) -> "BonusSummaryContext":
        aliases: dict[str, str] = {}
        scoring_engine = getattr(window, "scoring_engine", None)
        if scoring_engine:
            aliases.update(getattr(scoring_engine, "stat_alias_mapping", {}) or {})
        stats_config = getattr(window, "stats_config", None)
        if isinstance(stats_config, dict):
            aliases.update(stats_config.get("stat_alias_mapping", {}) or {})

        custom_weapons = dict(getattr(window, "_allocation_custom_weapons", {}) or {})
        for role_name, plan in (getattr(window, "final_plan", {}) or {}).items():
            if isinstance(plan, dict) and plan.get(PLAN_CUSTOM_WEAPON):
                custom_weapons[role_name] = plan[PLAN_CUSTOM_WEAPON]
        return cls(
            roles_db=getattr(window, "roles_db", {}) or {},
            shape_areas=getattr(window, "_shape_areas", {}) or {},
            stats_config=stats_config if isinstance(stats_config, dict) else {},
            stat_alias_mapping=aliases,
            weapons_db=getattr(window, "weapons_db", {}) or {},
            custom_weapons=custom_weapons,
        )


def _character_context(ctx: BonusSummaryContext) -> CharacterStatContext:
    return CharacterStatContext(
        role_models={},
        roles_db=ctx.roles_db,
        weapons_db=ctx.weapons_db,
        shape_areas=ctx.shape_areas,
        stats_config=ctx.stats_config,
        stat_alias_mapping=ctx.stat_alias_mapping,
        custom_weapons=ctx.custom_weapons,
    )


# Compatibility exports for result-view mixins.  The implementation is owned
# by stat_engine so role and allocation pages cannot diverge again.
def item_value(item, key, default=None):
    return _engine_item_value(item, key, default)


def stat_number_value(value) -> float:
    return _engine_stat_number_value(value)


def quality_coef(quality) -> float:
    return _engine_quality_coef(quality)


def canonical_stat_name(stat, stat_alias_mapping: dict[str, str]) -> str:
    return _engine_canonical_stat_name(stat, stat_alias_mapping)


def add_stat_total(totals: dict[str, float], stat, value, stat_alias_mapping: dict[str, str]) -> None:
    _engine_add_stat_total(totals, stat, value, stat_alias_mapping)


def fallback_tape_main_value(main_stat, quality, stats_config: dict[str, Any], stat_alias_mapping: dict[str, str]) -> float:
    return _engine_fallback_tape_main_value(main_stat, quality, stats_config, stat_alias_mapping)


def extra_shape_area(role_name: str, roles_db: dict[str, Any]) -> int | None:
    label = str((roles_db.get(role_name, {}) or {}).get("extra_shape_label", ""))
    match = re.search(r"(\d+)", label)
    return int(match.group(1)) if match else None


def split_loadout_sources(sources) -> tuple[Any | None, list[Any]]:
    tape = None
    drives = []
    for item in sources or []:
        if not item:
            continue
        item_type = str(item_value(item, EQUIP_TYPE, "") or "")
        has_tape_main = item_value(item, EQUIP_MAIN_STATS, None)
        has_shape = item_value(item, EQUIP_SHAPE_ID, None)
        if item_type == "tape" or (isinstance(item, dict) and has_tape_main and not has_shape):
            tape = item
        else:
            drives.append(item)
    return tape, drives


def loadout_uids(tape, drives) -> set[str]:
    uids: set[str] = set()
    for item in [tape, *(drives or [])]:
        uid = str(item_value(item, EQUIP_UID, "") or "") if item else ""
        if uid:
            uids.add(uid)
    return uids


def resolve_comparison_role_diff(window, role_name: str) -> dict[str, Any]:
    plan_diffs = getattr(window, "allocation_plan_diff", {}) or {}
    role_diff = plan_diffs.get(role_name, {}) or {}
    if role_diff.get(DIFF_CHANGED):
        return role_diff
    role_data = (getattr(window, "equipped_state", {}) or {}).get(role_name, {})
    if isinstance(role_data, dict):
        last_diff = role_data.get(ROLE_LAST_DIFF, {}) or {}
        if last_diff.get(DIFF_CHANGED):
            return last_diff
    return role_diff


def collect_added_uids(role_diff: dict[str, Any]) -> set[str]:
    result = {str(uid) for uid in (role_diff.get(DIFF_ADDED_UIDS, set()) or set()) if uid}
    for item in role_diff.get(DIFF_ADDED, []) or []:
        uid = str(item.get(EQUIP_UID, "") or "") if isinstance(item, dict) else ""
        if uid:
            result.add(uid)
    return result


def equipment_bonus_rows(ctx: BonusSummaryContext, role_name: str, tape, drives) -> BonusRows:
    totals = allocation_equipment_stats(_character_context(ctx), role_name, tape, drives)
    return sorted(((stat, value) for stat, value in totals.items() if value), key=lambda item: item[1], reverse=True)


def role_base_bonus_rows(ctx: BonusSummaryContext, role_name: str) -> BonusRows:
    panel = build_character_panel(_character_context(ctx), role_name)
    return sorted(panel.totals.items(), key=lambda item: item[1], reverse=True)


def merge_bonus_row_lists(ctx: BonusSummaryContext, *sources) -> BonusRows:
    totals: dict[str, float] = {}
    for rows in sources:
        for stat, value in rows or []:
            add_stat_total(totals, stat, value, ctx.stat_alias_mapping)
    return sorted(((stat, value) for stat, value in totals.items() if value), key=lambda item: item[1], reverse=True)


def synthesize_character_bonus_rows(rows) -> BonusRows:
    """Legacy helper: return panel-style total HP/attack/defence rows."""
    totals = {stat: float(value) for stat, value in (rows or [])}
    for base_key, pct_key, flat_key, total_label, flat_label in _CHARACTER_STAT_SYNTHESIS:
        base, pct, flat = totals.get(base_key, 0.0), totals.get(pct_key, 0.0), totals.get(flat_key, 0.0)
        if base or pct or flat:
            totals[total_label] = round(base * (1.0 + pct / 100.0) + flat, 4)
        totals.pop(base_key, None)
        totals.pop(pct_key, None)
        if flat_key in totals:
            totals[flat_label] = totals.pop(flat_key)
    return sorted(((stat, value) for stat, value in totals.items() if value), key=lambda item: item[1], reverse=True)


def bonus_rows_for_mode(ctx: BonusSummaryContext, role_name: str, tape, drives, mode: str = "equipment") -> BonusRows:
    if mode != "character":
        return equipment_bonus_rows(ctx, role_name, tape, drives)
    panel = build_character_panel(_character_context(ctx), role_name, tape, drives)
    return sorted(panel.totals.items(), key=lambda item: item[1], reverse=True)


def bonus_summary_mode_label(mode: str) -> str:
    return "角色属性汇总" if mode == "character" else "空幕属性汇总"


def bonus_uses_percent(stat) -> bool:
    normalized = str(stat or "").replace("%", "").strip()
    return "%" in str(stat) or normalized in {"暴击率", "暴击几率"} or "伤害增强" in str(stat) or "治疗加成" in str(stat)


def format_bonus_value(stat, value) -> str:
    if bonus_uses_percent(stat):
        return f"+{value:.2f}%"
    return f"+{value:.0f}" if abs(value - round(value)) < 0.01 else f"+{value:.2f}"


def format_bonus_delta_value(stat, delta) -> str:
    sign = "+" if delta >= 0 else ""
    if bonus_uses_percent(stat):
        return f"{sign}{delta:.2f}%"
    return f"{sign}{delta:.0f}" if abs(delta - round(delta)) < 0.01 else f"{sign}{delta:.2f}"


def is_crit_rate_stat(stat) -> bool:
    return str(stat or "").replace("%", "").strip() in {"暴击率", "暴击几率"}


def stats_match(stat, stat_key) -> bool:
    left = str(stat or "").replace("%", "").strip()
    right = str(stat_key or "").replace("%", "").strip()
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    left_alias = _CHARACTER_STAT_ALIASES.get(str(stat or "").strip())
    right_alias = _CHARACTER_STAT_ALIASES.get(str(stat_key or "").strip())
    return bool(
        (left_alias and (left_alias.replace("%", "").strip() == right or left_alias == str(stat_key or "").strip()))
        or (right_alias and (right_alias.replace("%", "").strip() == left or right_alias == str(stat or "").strip()))
    )


def is_highlighted_bonus_stat(stat, priority_stats=None) -> bool:
    return is_crit_rate_stat(stat) or any(stats_match(stat, key) for key in (priority_stats or []))


def has_bonus_delta(item: AlignedBonusRow) -> bool:
    delta = float(item.get("delta") or 0.0)
    return abs(delta) >= 0.0001 and not (item.get("old") is not None and item.get("old") == item.get("new"))


def sort_bonus_aligned_rows(aligned, priority_stats=None, prioritize_changed_only: bool = False) -> list[AlignedBonusRow]:
    priorities = list(priority_stats or [])

    def sort_key(item):
        stat = item.get("stat", "")
        if prioritize_changed_only and not has_bonus_delta(item):
            priority = None
        else:
            priority = next((index for index, key in enumerate(priorities) if stats_match(stat, key)), None)
            if priority is None and is_crit_rate_stat(stat):
                priority = len(priorities)
        if priority is not None:
            return (0, priority)
        return (1, -max(float(item.get("old") or 0.0), float(item.get("new") or 0.0)))

    return sorted(aligned or [], key=sort_key)


def aligned_bonus_comparison_rows(old_rows, new_rows, limit: int | None = None, changes_only: bool = False, priority_stats=None) -> list[AlignedBonusRow]:
    old_map, new_map = dict(old_rows or []), dict(new_rows or [])
    aligned: list[AlignedBonusRow] = []
    for stat in set(old_map) | set(new_map):
        old_value, new_value = old_map.get(stat), new_map.get(stat)
        delta = round((new_value or 0.0) - (old_value or 0.0), 4)
        aligned.append({"stat": stat, "old": old_value, "new": new_value, "delta": delta})
    if changes_only:
        aligned = [item for item in aligned if has_bonus_delta(item)]
    aligned = sort_bonus_aligned_rows(aligned, priority_stats, prioritize_changed_only=changes_only)
    return aligned[:limit] if limit is not None else aligned
