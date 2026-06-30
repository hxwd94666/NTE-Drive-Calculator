# 读取和保存角色功能所需的配置文件。
"""角色功能模块 - 数据访问层 (DAO)
负责所有与文件相关的读写操作，不包含业务逻辑。
"""

import json
import shutil
from pathlib import Path
from typing import Any

from src.utils.name_resolver import resolve_name

from .paths import (
    get_my_roles_path,
    get_my_roles_model_path,
    get_stats_path,
    get_weapon_path,
    get_tape_path,
    get_role_order_path,
    get_user_account_config_dir,
    get_roles_path,
)

EMPTY_SET_NAMES = {"", "未设置", "未指定", "未知套装", "卡带"}


# ==================== my_roles.json ====================

def _is_empty_set_name(value: Any) -> bool:
    return str(value or "").strip() in EMPTY_SET_NAMES


def _resolve_tape_template(set_name: str, tapes_data: dict) -> tuple[str, dict] | tuple[None, None]:
    if not set_name or not tapes_data:
        return None, None
    if set_name in tapes_data:
        return set_name, tapes_data[set_name] or {}

    display_to_key = {
        str(template.get("display_name", "") or key): key
        for key, template in tapes_data.items()
        if isinstance(template, dict)
    }
    if set_name in display_to_key:
        key = display_to_key[set_name]
        return key, tapes_data[key] or {}

    resolved_key = resolve_name(set_name, list(tapes_data.keys()), cutoff=0.78)
    if resolved_key:
        return resolved_key, tapes_data[resolved_key] or {}

    resolved_display = resolve_name(set_name, list(display_to_key.keys()), cutoff=0.78)
    if resolved_display:
        key = display_to_key[resolved_display]
        return key, tapes_data[key] or {}
    return None, None


def _candidate_set_names(role_data: dict, set_bonus: dict) -> list[str]:
    tape = role_data.get("tape", {})
    if not isinstance(tape, dict):
        tape = {}
    candidates = [
        set_bonus.get("display_name"),
        set_bonus.get("set_name"),
        tape.get("set_name"),
        tape.get("display_name"),
    ]
    return [str(name).strip() for name in candidates if not _is_empty_set_name(name)]


def _repair_set_bonus_data(data: dict) -> tuple[dict, bool]:
    if not isinstance(data, dict):
        return data, False
    tapes_data = load_tapes()
    changed = False
    for role_data in data.values():
        if not isinstance(role_data, dict):
            continue
        set_bonus = role_data.get("set_bonus")
        if not isinstance(set_bonus, dict):
            set_bonus = {"display_name": "", "skill": {}, "skill_2": {}, "skill_cover": 0.8}
            role_data["set_bonus"] = set_bonus
            changed = True

        selected_name = next(iter(_candidate_set_names(role_data, set_bonus)), "")
        resolved_name, template = _resolve_tape_template(selected_name, tapes_data)
        template = template or {}
        display_name = template.get("display_name") or resolved_name or selected_name

        if display_name and _is_empty_set_name(set_bonus.get("display_name")):
            set_bonus["display_name"] = display_name
            changed = True
        if resolved_name and not set_bonus.get("set_name"):
            set_bonus["set_name"] = resolved_name
            changed = True

        for key in ("skill", "skill_2"):
            if not isinstance(set_bonus.get(key), dict):
                set_bonus[key] = {}
                changed = True
            if not set_bonus[key] and isinstance(template.get(key), dict) and template.get(key):
                set_bonus[key] = dict(template[key])
                changed = True

        if "skill_cover" not in set_bonus:
            set_bonus["skill_cover"] = float(template.get("skill_cover", 0.8))
            changed = True
    return data, changed

def load_my_roles() -> dict:
    """
    加载 my_roles.json 数据。
    如果文件不存在，尝试从模板文件复制。
    """
    filepath = get_my_roles_path()
    model_path = get_my_roles_model_path()

    if not filepath.exists() and model_path.exists():
        shutil.copy(model_path, filepath)

    if not filepath.exists():
        return {}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        data, changed = _repair_set_bonus_data(data)
        if changed:
            save_my_roles(data)
        return data
    except (json.JSONDecodeError, IOError):
        return {}


def save_my_roles(data: dict) -> bool:
    """保存 my_roles.json，返回是否成功"""
    try:
        filepath = get_my_roles_path()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True
    except IOError:
        return False


# ==================== role_order.json ====================

def load_role_order() -> list:
    """加载角色顺序列表，若文件不存在或格式错误返回空列表"""
    path = get_role_order_path()
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_role_order(order: list) -> bool:
    """保存角色顺序到 role_order.json（覆盖写入）"""
    try:
        path = get_role_order_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(order, f, ensure_ascii=False, indent=2)
        return True
    except IOError:
        return False


# ==================== roles.json ====================
def load_role(role_name: str) -> dict:
    """加载 stats.json（词条配置源），文件不存在时返回空字典"""
    filepath = get_roles_path()
    if not filepath.exists():
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            roles = json.load(f)
            return roles.get(role_name, {})
    except (json.JSONDecodeError, IOError):
        return {}


# ==================== stats.json ====================

def load_stats() -> dict:
    """加载 stats.json（词条配置源），文件不存在时返回空字典"""
    filepath = get_stats_path()
    if not filepath.exists():
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


# ==================== weapons.json ====================

def load_weapons() -> dict:
    """加载 weapons.json（弧盘数据库）"""
    filepath = get_weapon_path()
    if not filepath.exists():
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


# ==================== tapes.json ====================

def load_tapes() -> dict:
    """加载 tapes.json（空幕数据库）"""
    filepath = get_tape_path()
    if not filepath.exists():
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


# ==================== real_inventory.json ====================

def load_real_inventory() -> dict:
    """加载 real_inventory.json（用户真实背包）"""
    filepath = get_user_account_config_dir() / "real_inventory.json"
    if not filepath.exists():
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


# ==================== 模型文件合并 ====================

def merge_new_roles_from_model() -> dict:
    """
    从 my_roles_model.json 中读取并合并新角色到 my_roles.json
    返回合并后的完整数据字典，如果没有新角色则返回原有数据
    """
    model_path = get_my_roles_model_path()
    if not model_path.exists():
        return load_my_roles()  # 直接返回现有数据

    try:
        with open(model_path, "r", encoding="utf-8") as f:
            model_data = json.load(f)
        if not isinstance(model_data, dict):
            return load_my_roles()
    except (json.JSONDecodeError, IOError):
        return load_my_roles()

    # 加载现有数据
    current_data = load_my_roles()
    existing_names = set(current_data.keys())

    new_roles = {}
    for name, role_data in model_data.items():
        if name not in existing_names:
            new_roles[name] = role_data
            existing_names.add(name)

    if new_roles:
        current_data.update(new_roles)
        save_my_roles(current_data)

    return current_data
