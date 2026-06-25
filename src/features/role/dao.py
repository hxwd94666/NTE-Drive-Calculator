"""角色功能模块 - 数据访问层 (DAO)
负责所有与文件相关的读写操作，不包含业务逻辑。
"""

import json
import shutil
from pathlib import Path
from typing import Any

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


# ==================== my_roles.json ====================

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
            return json.load(f)
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