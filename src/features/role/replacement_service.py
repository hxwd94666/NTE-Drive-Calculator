# 计算角色装备替换候选和替换写回计划。
"""Business services for role equipment replacement dialogs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .core import calc_base_damage, get_character_total_stats, get_valid_drives, is_empty_drive


ScoreDriveFunc = Callable[[dict, str, dict, str], float]


@dataclass(frozen=True)
class DriveReplacementCandidate:
    drive: dict
    score: float
    margin: float
    used_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class DriveReplacementOptions:
    current_shape: str
    current_uid: str
    current_score: float
    current_margin: float
    equipped_drives: list[dict]
    user_map: dict[str, list[str]]
    candidates: list[DriveReplacementCandidate]


@dataclass(frozen=True)
class DriveReplacementPlan:
    role_name: str
    current_uid: str
    new_drive: dict
    new_entry: dict
    displaced_roles: tuple[str, ...]


def equipment_user_map(my_roles_data: dict, role_name: str, item_kind: str) -> dict[str, list[str]]:
    user_map: dict[str, list[str]] = {}
    for rn, rdata in (my_roles_data or {}).items():
        if rn == role_name or not isinstance(rdata, dict):
            continue
        if item_kind == "tape":
            tape = rdata.get("tape", {})
            uid = tape.get("uid") if isinstance(tape, dict) else ""
            if uid:
                user_map.setdefault(uid, []).append(rn)
            continue
        for drive in rdata.get("drive", {}).get("drives", []) or []:
            uid = drive.get("uid")
            if uid:
                user_map.setdefault(uid, []).append(rn)
    return user_map


def keep_top_candidates_with_unassigned(
    candidates,
    user_map: dict,
    uid_getter,
    top_limit: int = 20,
    min_unassigned: int = 3,
):
    final = list(candidates[:top_limit])
    unassigned_count = sum(1 for entry in final if uid_getter(entry) not in user_map)
    if unassigned_count >= min_unassigned:
        return final
    for entry in candidates[top_limit:]:
        if uid_getter(entry) not in user_map:
            final.append(entry)
            unassigned_count += 1
            if unassigned_count >= min_unassigned:
                break
    return final


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


def build_drive_replacement_options(
    *,
    role_name: str,
    role_data: dict,
    current_drive: dict,
    inventory: list[dict] | None,
    my_roles_data: dict,
    weights: dict,
    score_drive: ScoreDriveFunc | None,
) -> DriveReplacementOptions | None:
    if not inventory:
        return None

    current_shape = current_drive.get("shape_id", "")
    current_uid = current_drive.get("uid", "")
    equipped_drives = role_data.get("drive", {}).get("drives", [])
    equipped_uids = {drive.get("uid", "") for drive in equipped_drives}
    user_map = equipment_user_map(my_roles_data, role_name, "drive")

    if score_drive:
        current_score = score_drive(
            current_drive.get("sub_stats", {}),
            current_shape,
            weights,
            current_drive.get("quality", "Gold"),
        )
    else:
        current_score = 0.0

    raw_candidates = [
        drive for drive in inventory
        if drive.get("shape_id") == current_shape
        and drive.get("uid") not in equipped_uids
        and drive.get("uid") != current_uid
    ]
    scored = []
    for drive in raw_candidates:
        score = score_drive(
            drive.get("sub_stats", {}),
            drive.get("shape_id", ""),
            weights,
            drive.get("quality", "Gold"),
        ) if score_drive else 0.0
        scored.append((score, drive))
    scored.sort(key=lambda item: item[0], reverse=True)
    selected = keep_top_candidates_with_unassigned(scored, user_map, lambda entry: entry[1].get("uid", ""))

    candidates = [
        DriveReplacementCandidate(
            drive=drive,
            score=score,
            margin=calc_drive_replacement_margin(role_data, equipped_drives, current_uid, drive),
            used_by=tuple(user_map.get(drive.get("uid", ""), [])),
        )
        for score, drive in selected
    ]
    return DriveReplacementOptions(
        current_shape=current_shape,
        current_uid=current_uid,
        current_score=current_score,
        current_margin=calc_single_drive_margin(role_data, current_drive),
        equipped_drives=equipped_drives,
        user_map=user_map,
        candidates=candidates,
    )


def build_drive_replacement_plan(role_name: str, current_uid: str, new_drive: dict, user_map: dict[str, list[str]]) -> DriveReplacementPlan:
    new_entry = {
        "uid": new_drive["uid"],
        "shape_id": new_drive["shape_id"],
        "sub_stats": new_drive["sub_stats"],
        "quality": new_drive.get("quality", "Gold"),
        "is_changed": True,
        "display_name": f"{new_drive['shape_id']}-" + "|".join(
            f"{key}_{value}" for key, value in new_drive["sub_stats"].items()
        ),
    }
    return DriveReplacementPlan(
        role_name=role_name,
        current_uid=current_uid,
        new_drive=new_drive,
        new_entry=new_entry,
        displaced_roles=tuple(user_map.get(new_drive.get("uid", ""), [])),
    )


def apply_drive_replacement_plan(form_data: dict, role_data: dict, plan: DriveReplacementPlan) -> tuple[bool, set[str]]:
    drives_list = role_data["drive"]["drives"]
    idx = next((i for i, drive in enumerate(drives_list) if drive.get("uid") == plan.current_uid), None)
    if idx is None:
        return False, set()

    dirty_equipment_roles = {plan.role_name}
    drives_list[idx] = dict(plan.new_entry)

    new_uid = plan.new_drive["uid"]
    for other_role in plan.displaced_roles:
        other_drives = form_data.get(other_role, {}).get("drive", {}).get("drives", [])
        for index, old_drive in enumerate(other_drives):
            if old_drive.get("uid") == new_uid:
                other_drives[index] = {
                    "uid": f"empty_{new_uid}",
                    "shape_id": old_drive.get("shape_id", ""),
                    "sub_stats": {},
                    "quality": "Gold",
                    "is_changed": True,
                    "display_name": f"{old_drive.get('shape_id', '')}-(空)",
                }
                dirty_equipment_roles.add(other_role)
                break
    return True, dirty_equipment_roles
