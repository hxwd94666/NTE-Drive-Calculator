# 管理扫描专属配置的账号读写与旧文件回退；不读取仓库设置或创建界面。
from pathlib import Path
from src.domain.inventory.post_actions import default_post_action_config
from src.domain.equipment.eligibility.management_settings import normalize_scan_management_config
from src.storage.json_store import read_json, write_json
from src.services.accounts.settings import AccountSettingsService
from src.storage.sqlite.user_data_dao import UserDataDao


def scan_post_action_config_path(user_config_dir: Path) -> Path:
    if user_config_dir is None:
        raise ValueError("user_config_dir is required")
    return Path(user_config_dir) / "scan_post_actions.json"


def load_scan_post_action_config(
    user_config_dir: Path | None,
    *,
    user_database_path: Path | None = None,
) -> dict:
    if user_database_path is not None:
        preferences = AccountSettingsService(user_database_path).load("ui")
        stored = preferences.get("full_scan_post_action_config")
        if isinstance(stored, dict):
            return normalize_scan_management_config(stored)
    if user_config_dir is None:
        return default_post_action_config()
    legacy = read_json(scan_post_action_config_path(user_config_dir), default=None)
    config = normalize_scan_management_config(legacy)
    if user_database_path is not None and isinstance(legacy, dict) and legacy:
        save_scan_post_action_config(
            user_config_dir,
            config,
            user_database_path=user_database_path,
        )
    return config


def save_scan_post_action_config(
    user_config_dir: Path | None,
    config: dict,
    *,
    user_database_path: Path | None = None,
) -> None:
    normalized = normalize_scan_management_config(config)
    if user_database_path is not None:
        # 扫描仅提交自身字段，不能将旧 UI 分组草稿写回仓库管理或其他账号偏好。
        with UserDataDao(user_database_path) as dao:
            dao.patch_application_setting_copy("ui", {"full_scan_post_action_config": normalized})
        return
    if user_config_dir is None:
        raise ValueError("user_config_dir is required when account storage is unavailable")
    write_json(scan_post_action_config_path(user_config_dir), normalized, indent=2)
