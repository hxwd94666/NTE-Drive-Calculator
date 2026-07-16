# 暴击率阈值与配装累计暴击计算。
"""Crit rate accumulation and rank adjustments for role-priority allocation."""

from __future__ import annotations

import re
from typing import Any

from src.domain.grade_limits import GRADE_LADDER, meets_min_grade

CRIT_STAT = "暴击率%"
CRIT_RANK_BONUS = 100_000.0
DEFAULT_CRIT_THRESHOLD = 5.0


def _item_value(item: Any, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _stat_number_value(value) -> float:
    try:
        return float(str(value).replace("%", "").strip())
    except Exception:
        return 0.0


def _canonical_stat_name(stat: str, alias_mapping: dict | None = None) -> str:
    stat = str(stat or "").strip()
    if not stat:
        return ""
    aliases = alias_mapping or {}
    return aliases.get(stat, stat)


def is_crit_stat(stat: str, alias_mapping: dict | None = None) -> bool:
    canonical = _canonical_stat_name(stat, alias_mapping)
    normalized = canonical.replace("%", "")
    return normalized == "暴击率" or canonical == CRIT_STAT


def _add_crit_total(totals: dict[str, float], stat: str, value, alias_mapping: dict | None = None) -> None:
    canonical = _canonical_stat_name(stat, alias_mapping)
    numeric = _stat_number_value(value)
    if not canonical or numeric == 0:
        return
    totals[canonical] = round(totals.get(canonical, 0.0) + numeric, 4)


def _fallback_tape_main_value(main_stat: str, quality: str = "Gold", tape_main_values: dict | None = None) -> float:
    configured = tape_main_values or {}
    main_stat = str(main_stat or "").strip()
    quality_coef = {"Gold": 1.0, "Purple": 0.8, "Blue": 0.6}.get(str(quality or "Gold"), 1.0)
    if main_stat in configured:
        return _stat_number_value(configured[main_stat]) * quality_coef
    if main_stat.replace("%", "") in {"暴击率", "暴击率%"} or main_stat == CRIT_STAT:
        return 30.0 * quality_coef
    return 0.0


def _extra_shape_area(role_data: dict) -> int | None:
    label = str(role_data.get("extra_shape_label", "") or "")
    match = re.search(r"(\d+)", label)
    return int(match.group(1)) if match else None


def _drive_area(drive: Any, shape_areas: dict | None = None) -> int:
    area = _item_value(drive, "area", None)
    if area is not None:
        return int(area or 0)
    shape_id = str(_item_value(drive, "shape_id", "") or "")
    if shape_areas and shape_id in shape_areas:
        try:
            return int(shape_areas.get(shape_id, 0) or 0)
        except (TypeError, ValueError):
            pass
    numbers = re.findall(r"\d+", shape_id)
    return int(numbers[0]) if numbers else 0


def drive_has_crit(item: Any, alias_mapping: dict | None = None) -> bool:
    for stat in (_item_value(item, "sub_stats", {}) or {}).keys():
        if is_crit_stat(stat, alias_mapping):
            return True
    return False


def normalize_preference_config(config: dict | None) -> dict:
    if not isinstance(config, dict):
        return {}
    stats = [str(s) for s in config.get("stats", []) if s]
    min_grade = str(config.get("min_grade_limit") or "A").upper()
    if min_grade not in GRADE_LADDER:
        min_grade = "A"
    raw_threshold = config.get("crit_threshold", config.get("crit_min_threshold", DEFAULT_CRIT_THRESHOLD))
    try:
        crit_threshold = float(raw_threshold)
    except (TypeError, ValueError):
        crit_threshold = DEFAULT_CRIT_THRESHOLD
    crit_threshold = max(0.0, min(100.0, crit_threshold))
    return {
        "stats": stats,
        "equal_priority": bool(config.get("equal_priority", False)),
        "ignore_grade_limit": bool(config.get("ignore_grade_limit", False)),
        "min_grade_limit": min_grade,
        "crit_threshold": crit_threshold,
    }


def preference_config_active(config: dict | None) -> bool:
    if not isinstance(config, dict) or not config:
        return False
    normalized = normalize_preference_config(config)
    return bool(
        normalized.get("stats")
        or normalized.get("equal_priority")
        or normalized.get("ignore_grade_limit")
        or str(normalized.get("min_grade_limit", "A")).upper() != "A"
        or "crit_threshold" in config
        or "crit_min_threshold" in config
    )


def crit_floor_enabled(config: dict | None) -> bool:
    # 仅显式写入 crit_threshold / crit_min_threshold 时启用暴击下限与 greedy
    if not isinstance(config, dict) or not config:
        return False
    return "crit_threshold" in config or "crit_min_threshold" in config


def meets_preference_grade_limit(
    score: float,
    area: int,
    config: dict | None,
    *,
    require_active: bool = False,
) -> bool:
    if require_active and not preference_config_active(config):
        return False
    if not isinstance(config, dict):
        return meets_min_grade(score, area, "A")
    normalized = normalize_preference_config(config)
    if normalized.get("ignore_grade_limit"):
        return True
    return meets_min_grade(score, area, normalized.get("min_grade_limit", "A"))


def _dedupe_stats(stats) -> list[str]:
    clean: list[str] = []
    seen: set[str] = set()
    for stat in stats or []:
        name = str(stat or "").strip()
        if name and name not in seen:
            seen.add(name)
            clean.append(name)
    return clean


def _stat_priority_should_persist(normalized: dict) -> bool:
    has_custom_grade = (
        not normalized.get("ignore_grade_limit")
        and str(normalized.get("min_grade_limit", "A")).upper() != "A"
    )
    has_custom_crit = int(normalized.get("crit_threshold", DEFAULT_CRIT_THRESHOLD)) != int(DEFAULT_CRIT_THRESHOLD)
    return bool(
        normalized.get("stats")
        or has_custom_grade
        or has_custom_crit
        or normalized.get("equal_priority")
        or normalized.get("ignore_grade_limit")
    )


def persistable_stat_priority_config(
    cfg: dict,
    *,
    allowed_stats: set[str] | frozenset[str] | None = None,
    dedupe_stats: bool = False,
) -> dict | None:
    if not isinstance(cfg, dict):
        return None
    if dedupe_stats:
        stats = _dedupe_stats(cfg.get("stats", []))
    else:
        stats = [stat for stat in cfg.get("stats", []) if stat]
    if allowed_stats is not None:
        stats = [stat for stat in stats if stat in allowed_stats]
    payload = {
        "stats": stats,
        "equal_priority": bool(cfg.get("equal_priority", False)),
        "ignore_grade_limit": bool(cfg.get("ignore_grade_limit", False)),
        "min_grade_limit": cfg.get("min_grade_limit", "A"),
    }
    if "crit_threshold" in cfg:
        payload["crit_threshold"] = cfg["crit_threshold"]
    elif "crit_min_threshold" in cfg:
        payload["crit_min_threshold"] = cfg["crit_min_threshold"]
    normalized = normalize_preference_config(payload)
    if not _stat_priority_should_persist(normalized):
        return None
    return {
        "stats": normalized["stats"],
        "equal_priority": normalized["equal_priority"],
        "ignore_grade_limit": normalized["ignore_grade_limit"],
        "min_grade_limit": normalized["min_grade_limit"],
        "crit_threshold": int(normalized["crit_threshold"]),
    }


def crit_rank_adjustment(
    current_crit: float,
    drive_has_crit_stat: bool,
    threshold: float = DEFAULT_CRIT_THRESHOLD,
) -> float:
    if not drive_has_crit_stat:
        return 0.0
    if current_crit < threshold:
        return CRIT_RANK_BONUS
    return 0.0


def loadout_crit_total(
    role_data: dict,
    tape: Any | None,
    drives: list[Any] | None,
    *,
    alias_mapping: dict | None = None,
    tape_main_values: dict | None = None,
    shape_areas: dict | None = None,
) -> float:
    totals: dict[str, float] = {}
    if tape:
        main_stat = _item_value(tape, "main_stats", "")
        main_value = _item_value(tape, "main_value", None)
        if main_value is None:
            main_value = _fallback_tape_main_value(
                main_stat,
                _item_value(tape, "quality", "Gold"),
                tape_main_values,
            )
        _add_crit_total(totals, main_stat, main_value, alias_mapping)
        for stat, value in (_item_value(tape, "sub_stats", {}) or {}).items():
            _add_crit_total(totals, stat, value, alias_mapping)

    drives = list(drives or [])
    for drive in drives:
        for stat, value in (_item_value(drive, "sub_stats", {}) or {}).items():
            _add_crit_total(totals, stat, value, alias_mapping)

    extra_buffs = role_data.get("extra_shape_buffs", {}) or {}
    if not isinstance(extra_buffs, dict):
        extra_buffs = {}
    target_area = _extra_shape_area(role_data)
    matched_count = 0
    if target_area:
        for drive in drives:
            if _drive_area(drive, shape_areas) == target_area:
                matched_count += 1
    for stat, value in extra_buffs.items():
        if is_crit_stat(stat, alias_mapping):
            _add_crit_total(totals, stat, _stat_number_value(value) * matched_count, alias_mapping)

    crit_key = CRIT_STAT
    for stat, value in totals.items():
        if is_crit_stat(stat, alias_mapping):
            crit_key = stat
            break
    return totals.get(crit_key, totals.get(CRIT_STAT, 0.0))
