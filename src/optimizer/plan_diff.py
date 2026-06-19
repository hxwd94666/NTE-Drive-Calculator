# 计算新旧配装方案的装备变动。
"""Diff saved equipment snapshots against a newly generated allocation plan."""

from __future__ import annotations

from typing import Any


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
        "uid": str(item.get("uid", "")),
        "type": item_type,
        "display_name": str(item.get("display_name") or item.get("uid") or ""),
    }
    for key in ("shape_id", "set_name", "main_stats", "sub_stats", "quality", "score", "grade", "score_area", "area"):
        if key in item:
            snapshot[key] = item[key]
    return snapshot


def _plan_item_snapshot(item: Any, item_type: str, role_name: str) -> dict:
    uid = str(_value(item, "uid", ""))
    sub_stats = _value(item, "sub_stats", {}) or {}
    quality = _value(item, "quality", "Gold")
    area = int(_value(item, "area", 15 if item_type == "tape" else 0) or 0)
    role_scores = _value(item, "role_scores", {}) or {}
    score = round(float(role_scores.get(role_name, 0.0) or 0.0), 2)
    if item_type == "tape":
        set_name = _value(item, "set_name", "")
        main_stats = _value(item, "main_stats", "")
        display_name = (
            f"{set_name}-{main_stats}-"
            f"{_format_sub_stats(sub_stats)}"
        )
        return {
            "uid": uid,
            "type": item_type,
            "display_name": display_name,
            "set_name": set_name,
            "main_stats": main_stats,
            "sub_stats": sub_stats,
            "quality": quality,
            "score": score,
            "grade": _grade_tag(score, area),
            "score_area": area,
            "area": area,
        }
    else:
        shape_id = _value(item, "shape_id", "")
        display_name = f"{shape_id}-{_format_sub_stats(sub_stats)}"
        return {
            "uid": uid,
            "type": item_type,
            "display_name": display_name,
            "shape_id": shape_id,
            "sub_stats": sub_stats,
            "quality": quality,
            "score": score,
            "grade": _grade_tag(score, area),
            "score_area": area,
            "area": area,
        }


def _old_items(role_state: dict) -> dict[str, dict]:
    items = {}
    if isinstance(role_state, list):
        for uid in role_state:
            if uid:
                items[str(uid)] = {"uid": str(uid), "type": "equipment", "display_name": str(uid)}
        return items
    if not isinstance(role_state, dict):
        return items
    tape = role_state.get("equipped_tape")
    if isinstance(tape, dict) and tape.get("uid"):
        items[str(tape["uid"])] = _state_item_snapshot(tape, "tape")
    for drive in role_state.get("equipped_drives", []) or []:
        if isinstance(drive, dict) and drive.get("uid"):
            items[str(drive["uid"])] = _state_item_snapshot(drive, "drive")
    return items


def _new_items(plan: dict, role_name: str) -> dict[str, dict]:
    items = {}
    if not isinstance(plan, dict) or not plan.get("valid", True):
        return items
    tape = plan.get("assigned_tape")
    if tape and _value(tape, "uid"):
        snapshot = _plan_item_snapshot(tape, "tape", role_name)
        items[snapshot["uid"]] = snapshot
    for drive in list(plan.get("assigned_set_drives", []) or []) + list(plan.get("assigned_extra_drives", []) or []):
        if not drive or not _value(drive, "uid"):
            continue
        snapshot = _plan_item_snapshot(drive, "drive", role_name)
        items[snapshot["uid"]] = snapshot
    return items


def build_plan_diff(old_state: dict, final_plan: dict) -> dict[str, dict]:
    """Return per-role added/removed equipment between saved state and a new plan."""

    diffs = {}
    for role, plan in (final_plan or {}).items():
        old_by_uid = _old_items((old_state or {}).get(role, {}))
        new_by_uid = _new_items(plan, role)
        if not old_by_uid:
            diffs[role] = {"changed": False, "added_uids": set(), "added": [], "removed": []}
            continue
        old_uids = set(old_by_uid)
        new_uids = set(new_by_uid)
        added_uids = new_uids - old_uids
        removed_uids = old_uids - new_uids
        diffs[role] = {
            "changed": bool(added_uids or removed_uids),
            "added_uids": added_uids,
            "added": [item for uid, item in new_by_uid.items() if uid in added_uids],
            "removed": [item for uid, item in old_by_uid.items() if uid in removed_uids],
        }
    return diffs
