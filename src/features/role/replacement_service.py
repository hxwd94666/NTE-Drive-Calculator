# 计算角色装备替换候选和替换写回计划。
"""Business services for role equipment replacement dialogs."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
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
                users = user_map.setdefault(uid, [])
                if rn not in users:
                    users.append(rn)
            continue
        for drive in rdata.get("drive", {}).get("drives", []) or []:
            uid = drive.get("uid")
            if uid:
                users = user_map.setdefault(uid, [])
                if rn not in users:
                    users.append(rn)
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
    # This dialog is still the legacy weight-based replacement flow.  Keep its
    # candidate order aligned with the displayed stat score until the future
    # optimizer explicitly provides a damage scorer and a fixed context.
    ranked_by_damage = [
        (
            calc_drive_replacement_margin(role_data, equipped_drives, current_uid, drive),
            drive,
        )
        for drive in raw_candidates
    ]
    ranked_by_damage.sort(
        key=lambda entry: (
            -(
                score_drive(
                    entry[1].get("sub_stats", {}),
                    entry[1].get("shape_id", ""),
                    weights,
                    entry[1].get("quality", "Gold"),
                ) if score_drive else 0.0
            ),
            str(entry[1].get("uid", "")),
        )
    )
    selected = keep_top_candidates_with_unassigned(
        ranked_by_damage, user_map, lambda entry: entry[1].get("uid", "")
    )

    candidates = [
        DriveReplacementCandidate(
            drive=drive,
            score=(
                score_drive(
                    drive.get("sub_stats", {}), drive.get("shape_id", ""), weights,
                    drive.get("quality", "Gold"),
                ) if score_drive else 0.0
            ),
            margin=margin,
            used_by=tuple(user_map.get(drive.get("uid", ""), [])),
        )
        for margin, drive in selected
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
