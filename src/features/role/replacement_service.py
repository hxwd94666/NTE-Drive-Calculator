# 计算角色装备替换候选和替换写回计划。
"""Business services for role equipment replacement dialogs."""

from __future__ import annotations

from copy import deepcopy

from .core import calc_base_damage, get_character_total_stats, get_valid_drives, is_empty_drive


def equipment_user_map(
    role_states: dict,
    current_role_name: str,
    item_kind: str,
) -> dict[str, list[str]]:
    """Map equipment UIDs to other roles without duplicate legacy entries."""

    users_by_uid: dict[str, list[str]] = {}
    for role_name, role_data in (role_states or {}).items():
        if role_name == current_role_name or not isinstance(role_data, dict):
            continue
        if item_kind == "tape":
            tape = role_data.get("tape", {})
            items = [tape] if isinstance(tape, dict) else []
        else:
            items = role_data.get("drive", {}).get("drives", []) or []
        for item in items:
            uid = item.get("uid") if isinstance(item, dict) else None
            if not uid:
                continue
            users = users_by_uid.setdefault(str(uid), [])
            if role_name not in users:
                users.append(role_name)
    return users_by_uid


def calc_single_drive_margin(role_data: dict, drive_to_exclude: dict) -> float:
    if is_empty_drive(drive_to_exclude):
        return 0.0

    try:
        drive_data = role_data.get("drive", {})
        original_drives = drive_data.get("drives", [])
        valid_drives = get_valid_drives(original_drives)

        if drive_to_exclude:
            exclude_uid = drive_to_exclude.get("uid")
            if exclude_uid:
                filtered_drives = [drive for drive in valid_drives if drive.get("uid") != exclude_uid]
            else:
                filtered_drives = [drive for drive in valid_drives if drive is not drive_to_exclude]
        else:
            filtered_drives = valid_drives

        no_drive_data = {key: value for key, value in role_data.items() if key != "drive"}
        no_drive_data["drive"] = {"drives": filtered_drives}
        stats_without = get_character_total_stats(no_drive_data)
        damage_without = calc_base_damage(stats_without)

        stats_with = get_character_total_stats(role_data)
        damage_with = calc_base_damage(stats_with)

        if damage_without == 0:
            return 0.0
        return (damage_with / damage_without - 1) * 100
    except Exception:
        return 0.0


def calc_drive_replacement_margin(role_data: dict, equipped_drives: list[dict], current_uid: str, candidate_drive: dict) -> float:
    try:
        bp = role_data.get("drive", {}).get("blueprint_layout", [])
        sim_role_data = {key: value for key, value in role_data.items() if key != "drive"}
        current_valid_drives = get_valid_drives(equipped_drives)
        sim_drives = [drive for drive in current_valid_drives if drive.get("uid") != current_uid]
        sim_drives.append(
            {
                "uid": candidate_drive["uid"],
                "shape_id": candidate_drive["shape_id"],
                "sub_stats": candidate_drive["sub_stats"],
                "quality": candidate_drive.get("quality", "Gold"),
            }
        )
        sim_role_data["drive"] = {"drives": sim_drives, "blueprint_layout": bp}
        stats_with = get_character_total_stats(sim_role_data)
        damage_with = calc_base_damage(stats_with)

        exclude_drive_data = {key: value for key, value in sim_role_data.items() if key != "drive"}
        candidate_uid = candidate_drive["uid"]
        exclude_drives = [drive for drive in sim_drives if drive.get("uid") != candidate_uid]
        exclude_drive_data["drive"] = {"drives": exclude_drives, "blueprint_layout": bp}
        stats_without = get_character_total_stats(exclude_drive_data)
        damage_without = calc_base_damage(stats_without)
        if damage_without == 0:
            return 0.0
        return (damage_with / damage_without - 1) * 100
    except Exception:
        return 0.0


def build_equipment_role_context(
    base_role_data: dict | None,
    equipped_drives: list[dict],
    equipped_tape: dict | None,
    *,
    set_bonus: dict | None = None,
) -> dict:
    """Build the role-calculation input with equipment from one stable snapshot.

    The caller supplies the role's editable base/weapon context, while the drive
    and tape slots always come from the plan or snapshot being evaluated.  This
    keeps direct-damage replacement evaluation independent from legacy inventory
    files and makes the role page and the 配装 page use the same calculator input.
    """
    role_data = deepcopy(base_role_data) if isinstance(base_role_data, dict) else {}
    source_drive = role_data.get("drive") if isinstance(role_data.get("drive"), dict) else {}
    role_data["drive"] = {
        "drives": [dict(drive) for drive in equipped_drives if isinstance(drive, dict)],
        "blueprint_layout": list(source_drive.get("blueprint_layout") or []),
    }
    role_data["tape"] = dict(equipped_tape) if isinstance(equipped_tape, dict) else {}
    if set_bonus is not None:
        role_data["set_bonus"] = deepcopy(set_bonus)
    return role_data


def calc_tape_margin(role_data: dict) -> float:
    """Return the current tape's direct-damage contribution in percent."""
    tape = role_data.get("tape", {})
    if not isinstance(tape, dict) or not tape.get("uid") or str(tape.get("uid")).startswith("empty_"):
        return 0.0
    try:
        without_tape = {key: value for key, value in role_data.items() if key != "tape"}
        damage_without = calc_base_damage(get_character_total_stats(without_tape))
        damage_with = calc_base_damage(get_character_total_stats(role_data))
        return 0.0 if damage_without == 0 else (damage_with / damage_without - 1) * 100
    except Exception:
        return 0.0


def calc_tape_replacement_margin(role_data: dict, candidate_tape: dict) -> float:
    """Return a same-suit tape candidate's direct-damage contribution.

    Replacement candidates are restricted to the current tape suit, so the
    existing set bonus remains valid while only the real main/sub-stat values are
    substituted.
    """
    simulated = dict(role_data)
    simulated["tape"] = dict(candidate_tape)
    return calc_tape_margin(simulated)


def rank_replacement_candidates_by_damage(
    role_data: dict,
    item_kind: str,
    current_item: dict,
    candidates: list[dict],
) -> tuple[float, list[tuple[float, dict]]]:
    """Calculate and sort compatible replacement candidates by direct damage.

    The returned percentage follows the role page's ``直伤收益`` convention.
    It is intentionally the common ranking entry point for role and SQLite plan
    replacement dialogs.
    """
    if item_kind == "drive":
        drives = list((role_data.get("drive") or {}).get("drives") or [])
        current_uid = str(current_item.get("uid") or "")
        current_margin = calc_single_drive_margin(role_data, current_item)
        ranked = [
            (calc_drive_replacement_margin(role_data, drives, current_uid, candidate), candidate)
            for candidate in candidates
        ]
    elif item_kind == "tape":
        current_margin = calc_tape_margin(role_data)
        ranked = [(calc_tape_replacement_margin(role_data, candidate), candidate) for candidate in candidates]
    else:
        raise ValueError(f"不支持的装备类别：{item_kind}")
    return current_margin, sorted(ranked, key=lambda entry: entry[0], reverse=True)
