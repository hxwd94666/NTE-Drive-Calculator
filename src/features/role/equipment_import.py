# 将配装结果导入角色配置。
"""Helpers for importing allocation equipment into my_roles.json."""

from __future__ import annotations

import json
import shutil
from typing import Any

from src.app import runtime
from src.utils.logger import logger


def _value(item: Any, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _format_board(raw_board: list) -> list:
    formatted = []
    for row in raw_board or []:
        formatted_row = []
        for cell in row:
            if cell == -1:
                formatted_row.append("XX")
            elif cell == 0:
                formatted_row.append("0")
            else:
                formatted_row.append(str(cell))
        formatted.append(formatted_row)
    return formatted


def _drive_from_source(source: Any) -> dict:
    uid = _value(source, "uid", "")
    shape_id = _value(source, "shape_id", "")
    sub_stats = _value(source, "sub_stats", {}) or {}
    return {
        "uid": uid,
        "shape_id": shape_id,
        "sub_stats": sub_stats,
        "quality": _value(source, "quality", "Gold"),
        "display_name": _value(source, "display_name", "")
        or f"{shape_id}-" + "|".join(f"{k}_{v}" for k, v in sub_stats.items()),
    }


def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def tape_equipment_from_source(source: Any) -> dict | None:
    if not source:
        return None
    set_name = _value(source, "set_name", "")
    if not set_name:
        return None

    tapes_data = _load_json(runtime.CONFIG_DIR / "tapes.json", {})
    stats_data = _load_json(runtime.CONFIG_DIR / "stats.json", {})
    tape_main_stat_values = stats_data.get("tape_main_stat_values", {}) or {}
    stat_alias_mapping = stats_data.get("stat_alias_mapping", {}) or {}

    main_stat_name = _value(source, "main_stats", "")
    if isinstance(main_stat_name, dict):
        main_stat = main_stat_name
    else:
        main_stat_name = stat_alias_mapping.get(main_stat_name, main_stat_name)
        main_stat = (
            {main_stat_name: tape_main_stat_values[main_stat_name]}
            if main_stat_name in tape_main_stat_values
            else {}
        )

    return {
        "uid": _value(source, "uid", ""),
        "display_name": _value(source, "display_name", "") or set_name,
        "shape_id": "TAPE_15",
        "set_name": set_name,
        "quality": _value(source, "quality", "Gold"),
        "main_stats": main_stat,
        "sub_stats": _value(source, "sub_stats", {}) or {},
    }


def set_bonus_from_tape_source(source: Any) -> dict:
    set_name = _value(source, "set_name", "")
    if not set_name:
        return {"display_name": "", "skill": {}, "skill_2": {}, "skill_cover": 0.8}
    tapes_data = _load_json(runtime.CONFIG_DIR / "tapes.json", {})
    template = tapes_data.get(set_name, {}) or {}
    return {
        "display_name": template.get("display_name", set_name),
        "skill": template.get("skill", {}) or {},
        "skill_2": template.get("skill_2", {}) or {},
        "skill_cover": float(template.get("skill_cover", 0.8)),
    }


def equipment_from_saved_state(role_state: dict) -> tuple[list, list, dict | None]:
    bp_layout = list(role_state.get("blueprint_layout", []) or [])
    drives = [
        _drive_from_source(drive)
        for drive in role_state.get("equipped_drives", []) or []
        if _value(drive, "uid", "")
    ]
    tape = tape_equipment_from_source(role_state.get("equipped_tape"))
    return bp_layout, drives, tape


def _load_my_roles() -> dict:
    my_roles_path = runtime.USER_CONFIG_DIR / "my_roles.json"
    if not my_roles_path.exists():
        model_path = runtime.CONFIG_DIR / "my_roles_model.json"
        my_roles_path.parent.mkdir(parents=True, exist_ok=True)
        if model_path.exists():
            shutil.copy(model_path, my_roles_path)
    return _load_json(my_roles_path, {})


def _save_my_roles(data: dict) -> None:
    path = runtime.USER_CONFIG_DIR / "my_roles.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def _apply_role_equipment(
    my_roles: dict,
    role_name: str,
    bp_layout: list,
    drives: list,
    tape: dict | None,
    *,
    clear_missing_tape: bool = False,
) -> dict:
    role_entry = my_roles.setdefault(role_name, {})
    drive_data = role_entry.setdefault("drive", {})
    drive_data["blueprint_layout"] = bp_layout
    drive_data["drives"] = drives
    drive_data.setdefault("extra_shape_buffs", 0)

    info = {}
    for drive in drives:
        for stats in (drive.get("main_stats", {}), drive.get("sub_stats", {})):
            for key, value in stats.items():
                info[key] = info.get(key, 0.0) + float(value)
    drive_data["info"] = info

    if tape:
        normalized_tape = tape_equipment_from_source(tape) or tape
        tape = normalized_tape
        role_entry["tape"] = tape
        role_entry["set_bonus"] = set_bonus_from_tape_source(tape)
    elif clear_missing_tape:
        role_entry.pop("tape", None)
        role_entry.pop("set_bonus", None)

    return role_entry


def import_role_equipment(role_name: str, bp_layout: list, drives: list, tape: dict | None) -> dict:
    my_roles = _load_my_roles()
    _apply_role_equipment(my_roles, role_name, bp_layout, drives, tape)
    _save_my_roles(my_roles)
    logger.success(f"已导入 {role_name} 的配装（{len(bp_layout)} 蓝图，{len(drives)} 驱动）")
    return my_roles


def import_all_role_equipment(equipped_state: dict) -> dict:
    my_roles = _load_my_roles()
    imported = 0
    skipped = 0
    failed = []

    for role_name, role_state in sorted((equipped_state or {}).items()):
        if not isinstance(role_state, dict):
            skipped += 1
            continue
        try:
            bp_layout, drives, tape = equipment_from_saved_state(role_state)
            if not bp_layout and not drives and not tape:
                skipped += 1
                continue
            _apply_role_equipment(
                my_roles,
                role_name,
                bp_layout,
                drives,
                tape,
                clear_missing_tape=True,
            )
            imported += 1
        except Exception as exc:
            failed.append({"role": role_name, "error": str(exc)})

    if imported:
        _save_my_roles(my_roles)
    logger.success(f"批量导入配装完成：成功 {imported} 个，跳过 {skipped} 个，失败 {len(failed)} 个")
    return {
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "my_roles": my_roles,
    }
