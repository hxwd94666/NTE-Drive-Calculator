# 配装属性聚合，以及旧/新方案属性行对比。
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.features.allocation.plan_diff_pairing import drive_item_area
from src.features.role.dao import load_my_roles
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_TYPE,
    EQUIP_UID,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    ROLE_LAST_DIFF,
)

BonusRows = list[tuple[str, float]]
AlignedBonusRow = dict[str, Any]

_CHARACTER_STAT_SYNTHESIS = (
    # 白值 * (1 + 百分比) + 固定值；flat_key 合成后改成小攻/小生/小防展示名。
    ("攻击力白值", "攻击力%", "攻击力", "总攻击力", "小攻击"),
    ("生命白值", "生命值%", "生命值", "总生命值", "小生命"),
    ("防御力白值", "防御力%", "防御力", "总防御力", "小防御"),
)

_CHARACTER_STAT_ALIASES = {
    "总攻击力": "攻击力白值",
    "攻击力白值": "总攻击力",
    "小攻击": "攻击力",
    "攻击力": "小攻击",
    "总生命值": "生命白值",
    "生命白值": "总生命值",
    "小生命": "生命值",
    "生命值": "小生命",
    "总防御力": "防御力白值",
    "防御力白值": "总防御力",
    "小防御": "防御力",
    "防御力": "小防御",
}


@dataclass(frozen=True)
class BonusSummaryContext:
    roles_db: dict[str, Any]
    shape_areas: dict[str, Any]
    stats_config: dict[str, Any]
    stat_alias_mapping: dict[str, str]

    @classmethod
    def from_window(cls, window) -> BonusSummaryContext:
        aliases: dict[str, str] = {}
        scoring_engine = getattr(window, "scoring_engine", None)
        if scoring_engine:
            aliases.update(getattr(scoring_engine, "stat_alias_mapping", {}) or {})
        stats_config = getattr(window, "stats_config", None)
        if isinstance(stats_config, dict):
            aliases.update(stats_config.get("stat_alias_mapping", {}) or {})
        return cls(
            roles_db=getattr(window, "roles_db", {}) or {},
            shape_areas=getattr(window, "_shape_areas", {}) or {},
            stats_config=stats_config if isinstance(stats_config, dict) else {},
            stat_alias_mapping=aliases,
        )


def item_value(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def stat_number_value(value) -> float:
    try:
        return float(str(value).replace("%", "").strip())
    except Exception:
        return 0.0


def quality_coef(quality) -> float:
    return {"Gold": 1.0, "Purple": 0.8, "Blue": 0.6}.get(str(quality or "Gold"), 1.0)


def canonical_stat_name(stat, stat_alias_mapping: dict[str, str]) -> str:
    stat = str(stat or "").strip()
    if not stat:
        return ""
    return stat_alias_mapping.get(stat, stat)


def add_stat_total(totals: dict[str, float], stat, value, stat_alias_mapping: dict[str, str]) -> None:
    stat = canonical_stat_name(stat, stat_alias_mapping)
    value = stat_number_value(value)
    if not stat or value == 0:
        return
    totals[stat] = round(totals.get(stat, 0.0) + value, 4)


def fallback_tape_main_value(main_stat, quality, stats_config: dict[str, Any], stat_alias_mapping: dict[str, str]) -> float:
    configured = (stats_config or {}).get("tape_main_stat_values", {})
    main_stat = str(main_stat or "").strip()
    canonical = canonical_stat_name(main_stat, stat_alias_mapping)
    coef = quality_coef(quality)
    if main_stat in configured:
        return stat_number_value(configured[main_stat]) * coef
    if canonical in configured:
        return stat_number_value(configured[canonical]) * coef
    if canonical in {"暴击伤害%"}:
        return 60.0 * coef
    if canonical in {"暴击率%"}:
        return 30.0 * coef
    if canonical in {"攻击力%", "防御力%", "生命值%"}:
        return 37.5 * coef
    if canonical in {"环合强度", "倾陷强度"}:
        return 180.0 * coef
    if "治疗加成" in canonical:
        return 34.5 * coef
    if "伤害增强" in canonical:
        return 37.5 * coef
    return 0.0


def extra_shape_area(role_name: str, roles_db: dict[str, Any]) -> int | None:
    label = str(roles_db.get(role_name, {}).get("extra_shape_label", ""))
    match = re.search(r"(\d+)", label)
    return int(match.group(1)) if match else None


def split_loadout_sources(sources) -> tuple[Any | None, list[Any]]:
    tape = None
    drives = []
    for item in sources or []:
        if not item:
            continue
        item_type = str(item_value(item, EQUIP_TYPE, "") or "")
        main_stats = item_value(item, EQUIP_MAIN_STATS, None)
        shape_id = item_value(item, EQUIP_SHAPE_ID, None)
        if item_type == "tape" or (isinstance(item, dict) and main_stats and not shape_id):
            tape = item
        else:
            drives.append(item)
    return tape, drives


def loadout_uids(tape, drives) -> set[str]:
    uids: set[str] = set()
    uid = str(item_value(tape, EQUIP_UID, "") or "") if tape else ""
    if uid:
        uids.add(uid)
    for drive in drives or []:
        drive_uid = str(item_value(drive, EQUIP_UID, "") or "")
        if drive_uid:
            uids.add(drive_uid)
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
    added_uids = {str(uid) for uid in (role_diff.get(DIFF_ADDED_UIDS, set()) or set()) if uid}
    for item in role_diff.get(DIFF_ADDED, []) or []:
        if isinstance(item, dict):
            uid = str(item.get(EQUIP_UID, "") or "")
            if uid:
                added_uids.add(uid)
    return added_uids


def equipment_bonus_rows(ctx: BonusSummaryContext, role_name: str, tape, drives) -> BonusRows:
    totals: dict[str, float] = {}
    aliases = ctx.stat_alias_mapping
    if tape:
        main_stat = item_value(tape, EQUIP_MAIN_STATS, "")
        main_value = item_value(tape, "main_value", None)
        if main_value is None:
            main_value = fallback_tape_main_value(
                main_stat,
                item_value(tape, EQUIP_QUALITY, "Gold"),
                ctx.stats_config,
                aliases,
            )
        add_stat_total(totals, main_stat, main_value, aliases)
        for stat, value in (item_value(tape, EQUIP_SUB_STATS, {}) or {}).items():
            add_stat_total(totals, stat, value, aliases)
    drives = list(drives or [])
    for drive in drives:
        for stat, value in (item_value(drive, EQUIP_SUB_STATS, {}) or {}).items():
            add_stat_total(totals, stat, value, aliases)
    role_data = ctx.roles_db.get(role_name, {})
    extra_buffs = role_data.get("extra_shape_buffs", {}) or {}
    if isinstance(extra_buffs, dict) and len(extra_buffs) > 1:
        first_key = next(iter(extra_buffs))
        extra_buffs = {first_key: extra_buffs[first_key]}
    target_area = extra_shape_area(role_name, ctx.roles_db)
    matched_count = 0
    if target_area:
        for drive in drives:
            if drive_item_area(drive, ctx.shape_areas) == target_area:
                matched_count += 1
    for stat, value in extra_buffs.items():
        add_stat_total(totals, stat, stat_number_value(value) * matched_count, aliases)
    rows = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [(stat, value) for stat, value in rows if value]


def get_my_role_entry(role_name: str) -> dict[str, Any]:
    cache = load_my_roles()
    entry = cache.get(role_name, {}) if isinstance(cache, dict) else {}
    return entry if isinstance(entry, dict) else {}


def role_base_bonus_rows(ctx: BonusSummaryContext, role_name: str) -> BonusRows:
    role_entry = get_my_role_entry(role_name)
    totals: dict[str, float] = {}
    aliases = ctx.stat_alias_mapping
    for stat, value in (role_entry.get("sub_stats") or {}).items():
        add_stat_total(totals, stat, value, aliases)
    weapon = role_entry.get("weapon") or {}
    if isinstance(weapon, dict):
        for stat, value in (weapon.get("sub_stats") or {}).items():
            add_stat_total(totals, stat, value, aliases)
        for effect in weapon.get("skill") or []:
            if not isinstance(effect, dict):
                continue
            key = effect.get("key")
            if not key:
                continue
            try:
                value = float(effect.get("value", 0.0) or 0.0)
                cover = float(effect.get("cover", 0.8) or 0.8)
                num = float(effect.get("num", 1) or 1)
            except (TypeError, ValueError):
                continue
            effect_total = value * cover * num
            if effect_total:
                add_stat_total(totals, key, effect_total, aliases)
    rows = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [(stat, value) for stat, value in rows if value]


def merge_bonus_row_lists(ctx: BonusSummaryContext, *sources) -> BonusRows:
    totals: dict[str, float] = {}
    aliases = ctx.stat_alias_mapping
    for rows in sources:
        for stat, value in rows or []:
            add_stat_total(totals, stat, value, aliases)
    merged = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [(stat, value) for stat, value in merged if value]


def synthesize_character_bonus_rows(rows) -> BonusRows:
    totals = {stat: float(value) for stat, value in (rows or [])}
    for base_key, pct_key, flat_key, total_label, flat_label in _CHARACTER_STAT_SYNTHESIS:
        base = float(totals.get(base_key, 0.0) or 0.0)
        pct = float(totals.get(pct_key, 0.0) or 0.0)
        flat = float(totals.get(flat_key, 0.0) or 0.0)
        if base or pct or flat:
            totals[total_label] = round(base * (1.0 + pct / 100.0) + flat, 4)
        totals.pop(base_key, None)
        if flat_key in totals:
            totals[flat_label] = totals.pop(flat_key)
    merged = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [(stat, value) for stat, value in merged if value]


def bonus_rows_for_mode(ctx: BonusSummaryContext, role_name: str, tape, drives, mode: str = "equipment") -> BonusRows:
    equipment_rows = equipment_bonus_rows(ctx, role_name, tape, drives)
    if mode != "character":
        return equipment_rows
    merged = merge_bonus_row_lists(ctx, role_base_bonus_rows(ctx, role_name), equipment_rows)
    return synthesize_character_bonus_rows(merged)


def bonus_summary_mode_label(mode: str) -> str:
    return "角色属性汇总" if mode == "character" else "空幕属性汇总"


def bonus_uses_percent(stat) -> bool:
    return "%" in stat or "伤害增强" in stat or "治疗加成" in stat


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
    normalized = str(stat or "").replace("%", "").strip()
    return normalized in {"暴击率", "暴击率%"}


def stats_match(stat, stat_key) -> bool:
    left = str(stat or "").replace("%", "").strip()
    right = str(stat_key or "").replace("%", "").strip()
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    left_alias = _CHARACTER_STAT_ALIASES.get(str(stat or "").strip())
    right_alias = _CHARACTER_STAT_ALIASES.get(str(stat_key or "").strip())
    if left_alias and (left_alias.replace("%", "").strip() == right or left_alias == str(stat_key or "").strip()):
        return True
    if right_alias and (right_alias.replace("%", "").strip() == left or right_alias == str(stat or "").strip()):
        return True
    return False


def is_highlighted_bonus_stat(stat, priority_stats=None) -> bool:
    if is_crit_rate_stat(stat):
        return True
    for key in priority_stats or []:
        if stats_match(stat, key):
            return True
    return False


def has_bonus_delta(item: AlignedBonusRow) -> bool:
    delta = float(item.get("delta") or 0.0)
    if abs(delta) < 0.0001:
        return False
    old_val = item.get("old")
    new_val = item.get("new")
    if old_val is not None and new_val is not None and old_val == new_val:
        return False
    return True


def sort_bonus_aligned_rows(aligned, priority_stats=None, prioritize_changed_only: bool = False) -> list[AlignedBonusRow]:
    priority_stats = list(priority_stats or [])

    def priority_index(stat, item):
        if prioritize_changed_only and not has_bonus_delta(item):
            return None
        for idx, key in enumerate(priority_stats):
            if stats_match(stat, key):
                return idx
        if is_crit_rate_stat(stat):
            return len(priority_stats)
        return None

    def sort_key(item):
        stat = item.get("stat", "")
        idx = priority_index(stat, item)
        if idx is not None:
            return (0, idx)
        max_val = max(float(item.get("old") or 0.0), float(item.get("new") or 0.0))
        return (1, -max_val)

    return sorted(aligned or [], key=sort_key)


def aligned_bonus_comparison_rows(
    old_rows,
    new_rows,
    limit: int | None = None,
    changes_only: bool = False,
    priority_stats=None,
) -> list[AlignedBonusRow]:
    old_map = dict(old_rows or [])
    new_map = dict(new_rows or [])
    stats = set(old_map) | set(new_map)
    aligned: list[AlignedBonusRow] = []
    for stat in stats:
        old_val = old_map.get(stat)
        new_val = new_map.get(stat)
        if old_val is not None and new_val is not None:
            delta = round(new_val - old_val, 4)
        elif old_val is None and new_val is not None:
            delta = round(new_val, 4)
        elif new_val is None and old_val is not None:
            delta = round(-old_val, 4)
        else:
            delta = 0.0
        aligned.append({"stat": stat, "old": old_val, "new": new_val, "delta": delta})
    if changes_only:
        aligned = [item for item in aligned if has_bonus_delta(item)]
    aligned = sort_bonus_aligned_rows(aligned, priority_stats, prioritize_changed_only=changes_only)
    if limit is not None:
        aligned = aligned[:limit]
    return aligned
