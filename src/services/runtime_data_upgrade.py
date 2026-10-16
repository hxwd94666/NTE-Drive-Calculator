# 在组合根持锁和恢复点成立后升级各账号，避免未切换账号重复创建恢复点。
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from src.integrations.accounts.index_file import locked_account_index
from src.integrations.distribution.data_lifecycle import upgrade_entries
from src.integrations.distribution.data_inventory import account_databases
from src.integrations.distribution.data_roots import write_record
from src.integrations.distribution.lock import DataRootLease, reject_links
from src.integrations.distribution.metadata import DistributionError
from src.services.accounts.settings import AccountSettingsService
from src.services.preferences.global_theme_settings_service import GlobalThemeSettingsService
from src.storage.sqlite.shared_data_dao import SharedDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.runtime_data_snapshot import (
    backup_database, database_identity, legacy_import_identity_needs_repair, repair_legacy_import_identity,
)


def repair_legacy_account_identities(root: Path, lease: DataRootLease, *, confirm) -> Path | None:
    """历史导入只认已登记目录；确认后全量备份待修库，全部成功才继续启动。"""
    lease.require_root(root)
    with locked_account_index():
        databases = account_databases(root)
        if not databases:
            return None
        index = root / "accounts/accounts.json"
        raw_index = index.read_bytes()
        names = {row["id"]: str(row.get("name") or row["id"]) for row in json.loads(raw_index)["accounts"]}
        candidates = [(path, key, names[key]) for path, key in databases
                      if legacy_import_identity_needs_repair(path, key, names[key])]
        if not candidates:
            return None
        if not confirm(root, len(candidates)):
            raise DistributionError("已取消旧导入账号标识修复；原数据保留，未创建替代账号。")
        directory = root / "maintenance/account-identity-recovery"
        reject_links(directory)
        directory.mkdir(parents=True, exist_ok=True)
        recovery = directory / uuid4().hex
        recovery.mkdir()
        record = {"version": 1, "state": "preparing", "accounts": []}
        write_record(recovery / "record.json", record)
        changed = []
        try:
            target_index = recovery / "accounts/accounts.json"
            target_index.parent.mkdir()
            target_index.write_bytes(raw_index)
            # 所有库完成一致性备份和完整性检查后才写第一份身份；不复制活动 WAL/SHM。
            for path, key, name in candidates:
                backup = recovery / path.relative_to(root)
                backup.parent.mkdir(parents=True)
                backup_database(path, backup)
                record["accounts"].append({"path": path.relative_to(root).as_posix(), "before": "default", "after": key})
            record["state"] = "prepared"
            write_record(recovery / "record.json", record)
            if index.read_bytes() != raw_index:
                raise DistributionError("账号索引在修复前发生变化；保留备份，请重新启动。")
            for path, key, name in candidates:
                repair_legacy_import_identity(path, key, name)
                changed.append((path, key, name))
                database_identity(path, key)
            record["state"] = "completed"
            write_record(recovery / "record.json", record)
        except BaseException:
            try:
                for path, key, name in reversed(changed):
                    repair_legacy_import_identity(path, key, name, rollback=True)
                record["state"] = "failed_rolled_back"
            except BaseException:
                record["state"] = "recovery_required"
                raise
            finally:
                write_record(recovery / "record.json", record)
            raise
        return recovery


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
