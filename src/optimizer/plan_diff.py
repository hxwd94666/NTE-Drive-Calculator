# 计算新旧配装方案的装备变动。
"""Diff saved equipment snapshots against a newly generated allocation plan."""

from __future__ import annotations

from typing import Any

from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_AREA,
    EQUIP_DISPLAY_NAME,
    EQUIP_GRADE,
    EQUIP_ITEM_TYPE,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SCORE,
    EQUIP_SCORE_AREA,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_TYPE,
    EQUIP_UID,
    PLAN_ASSIGNED_TAPE,
    PLAN_SCORE,
    PLAN_VALID,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    plan_drives,
)


def _value(item: Any, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _format_sub_stats(sub_stats: dict | None) -> str:
    return "|".join(f"{key}_{value}" for key, value in (sub_stats or {}).items())


def _grade_tag(score: float, area: int) -> str:
    max_score = float(area or 0) * 10.0
    if max_score <= 0:
        return "D"
    ratio = float(score or 0.0) / max_score
    if ratio >= 0.8:
        return "ACE"
    if ratio >= 0.7:
        return "SSS"
    if ratio >= 0.6:
        return "SS"
    if ratio >= 0.5:
        return "S"
    if ratio >= 0.4:
        return "A"
    if ratio >= 0.3:
        return "B"
    if ratio >= 0.2:
        return "C"
    return "D"


def _state_item_snapshot(item: dict, item_type: str) -> dict:
    snapshot = {
        EQUIP_UID: str(item.get(EQUIP_UID, "")),
        EQUIP_TYPE: item_type,
        EQUIP_DISPLAY_NAME: str(item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID) or ""),
    }
    for key in (EQUIP_SHAPE_ID, EQUIP_SET_NAME, EQUIP_MAIN_STATS, EQUIP_SUB_STATS, EQUIP_QUALITY, EQUIP_SCORE, EQUIP_GRADE, EQUIP_SCORE_AREA, EQUIP_AREA):
        if key in item:
            snapshot[key] = item[key]
    return snapshot


def _plan_item_snapshot(item: Any, item_type: str, role_name: str) -> dict:
    uid = str(_value(item, EQUIP_UID, ""))
    sub_stats = _value(item, EQUIP_SUB_STATS, {}) or {}
    quality = _value(item, EQUIP_QUALITY, "Gold")
    area = int(_value(item, EQUIP_AREA, 15 if item_type == "tape" else 0) or 0)
    role_scores = _value(item, "role_scores", {}) or {}
    score = round(float(role_scores.get(role_name, _value(item, PLAN_SCORE, 0.0)) or 0.0), 2)
    if item_type == "tape":
        set_name = _value(item, EQUIP_SET_NAME, "")
        main_stats = _value(item, EQUIP_MAIN_STATS, "")
        display_name = (
            f"{set_name}-{main_stats}-"
            f"{_format_sub_stats(sub_stats)}"
        )
        return {
            EQUIP_UID: uid,
            EQUIP_TYPE: item_type,
            EQUIP_DISPLAY_NAME: display_name,
            EQUIP_SET_NAME: set_name,
            EQUIP_MAIN_STATS: main_stats,
            EQUIP_SUB_STATS: sub_stats,
            EQUIP_QUALITY: quality,
            EQUIP_SCORE: score,
            EQUIP_GRADE: _grade_tag(score, area),
            EQUIP_SCORE_AREA: area,
            EQUIP_AREA: area,
        }
    else:
        shape_id = _value(item, EQUIP_SHAPE_ID, "")
        display_name = f"{shape_id}-{_format_sub_stats(sub_stats)}"
        return {
            EQUIP_UID: uid,
            EQUIP_TYPE: item_type,
            EQUIP_DISPLAY_NAME: display_name,
            EQUIP_SHAPE_ID: shape_id,
            EQUIP_SUB_STATS: sub_stats,
            EQUIP_QUALITY: quality,
            EQUIP_SCORE: score,
            EQUIP_GRADE: _grade_tag(score, area),
            EQUIP_SCORE_AREA: area,
            EQUIP_AREA: area,
        }


def _old_items(role_state: dict) -> dict[str, dict]:
    items = {}
    if isinstance(role_state, list):
        for uid in role_state:
            if uid:
                items[str(uid)] = {EQUIP_UID: str(uid), EQUIP_TYPE: "equipment", EQUIP_DISPLAY_NAME: str(uid)}
        return items
    if not isinstance(role_state, dict):
        return items
    tape = role_state.get(ROLE_EQUIPPED_TAPE)
    if isinstance(tape, dict) and tape.get(EQUIP_UID):
        items[str(tape[EQUIP_UID])] = _state_item_snapshot(tape, "tape")
    for drive in role_state.get(ROLE_EQUIPPED_DRIVES, []) or []:
        if isinstance(drive, dict) and drive.get(EQUIP_UID):
            items[str(drive[EQUIP_UID])] = _state_item_snapshot(drive, "drive")
    return items


def _new_items(plan: dict, role_name: str) -> dict[str, dict]:
    items = {}
    if not isinstance(plan, dict) or not plan.get(PLAN_VALID, True):
        return items
    tape = plan.get(PLAN_ASSIGNED_TAPE)
    if tape and _value(tape, EQUIP_UID):
        snapshot = _plan_item_snapshot(tape, "tape", role_name)
        items[snapshot[EQUIP_UID]] = snapshot
    for drive in plan_drives(plan):
        if not drive or not _value(drive, EQUIP_UID):
            continue
        snapshot = _plan_item_snapshot(drive, "drive", role_name)
        items[snapshot[EQUIP_UID]] = snapshot
    return items


def build_plan_diff(old_state: dict, final_plan: dict) -> dict[str, dict]:
    """Return per-role added/removed equipment between saved state and a new plan."""

    diffs = {}
    for role, plan in (final_plan or {}).items():
        old_by_uid = _old_items((old_state or {}).get(role, {}))
        new_by_uid = _new_items(plan, role)
        if not old_by_uid:
            diffs[role] = {DIFF_CHANGED: False, DIFF_ADDED_UIDS: set(), DIFF_ADDED: [], DIFF_REMOVED: []}
            continue
        old_uids = set(old_by_uid)
        new_uids = set(new_by_uid)
        added_uids = new_uids - old_uids
        removed_uids = old_uids - new_uids
        diffs[role] = {
            DIFF_CHANGED: bool(added_uids or removed_uids),
            DIFF_ADDED_UIDS: added_uids,
            DIFF_ADDED: [item for uid, item in new_by_uid.items() if uid in added_uids],
            DIFF_REMOVED: [item for uid, item in old_by_uid.items() if uid in removed_uids],
        }
    return diffs
