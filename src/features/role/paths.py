# 提供角色功能相关配置文件路径。
from pathlib import Path
from src.app import runtime


def get_user_account_config_dir() -> Path:
    return runtime.USER_CONFIG_DIR


def get_my_roles_path() -> Path:
    return get_user_account_config_dir() / "my_roles.json"


def get_config_path() -> Path:
    return runtime.CONFIG_DIR


def get_my_roles_model_path() -> Path:
    return get_config_path() / "my_roles_model.json"


def get_roles_img_path(role_name: str) -> Path:
    return get_config_path() / "templates" / "roles" / f"{role_name}.png"


def get_stats_path() -> Path:
    return get_config_path() / "stats.json"


def get_roles_path() -> Path:
    return get_config_path() / "roles.json"


def get_tape_path() -> Path:
    return get_config_path() / "tapes.json"


def get_role_order_path() -> Path:
    return get_user_account_config_dir() / "role_order.json"
