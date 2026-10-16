# 在组合根持锁和恢复点成立后升级各账号，避免未切换账号重复创建恢复点。
from __future__ import annotations

import json
from pathlib import Path

from src.integrations.distribution.data_lifecycle import upgrade_entries
from src.integrations.distribution.lock import DataRootLease
from src.services.accounts.settings import AccountSettingsService
from src.services.preferences.global_theme_settings_service import GlobalThemeSettingsService
from src.storage.sqlite.shared_data_dao import SharedDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


def apply_runtime_data_upgrades(root: Path, package: Path, snapshot: Path | None, lease: DataRootLease) -> None:
    lease.require_root(root)
    if snapshot is None:
        return
    index = root / "accounts" / "accounts.json"
    active = json.loads(index.read_text(encoding="utf-8"))["active_account_id"] if index.exists() else None
    settings = []
    for entry in upgrade_entries(snapshot):
        path = root / entry["path"]
        if entry["account_id"] is None:
            with SharedDataDao(path):
                pass
        else:
            with UserDataDao(path):
                pass
            service = AccountSettingsService(path, static_database_path=package / "data" / "game_static.sqlite3",
                                             legacy_config_dir=path.parent / "config")
            settings.append((entry["account_id"], service))
    # 仅当前账号决定原主题迁移；其他账号升级不改全局选择。
    current = next((service for key, service in settings if key == active), None)
    if current is None and active is not None:
        active_root = root / "accounts" / active
        current = AccountSettingsService(active_root / "user_data.sqlite3",
                                         static_database_path=package / "data" / "game_static.sqlite3",
                                         legacy_config_dir=active_root / "config")
    legacy_theme = current.legacy_theme_preference() if current is not None else None
    GlobalThemeSettingsService(root / "config" / "global_ui_preferences.json").load(legacy_theme=legacy_theme)
    for _key, service in settings:
        service.migrate_legacy_settings()
        service.remove_legacy_theme_preference()
