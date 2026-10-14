# 验证养成历史随账号一致性导出导入，排除升级备份并保持账号身份隔离。
from __future__ import annotations

import zipfile

from src.features.accounts.manager import AccountManager
from src.services.accounts.transfer import export_account_data, import_account_data
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_cultivation_history import history_payload

NTE_TEST_TIER = "core"


def _manager(root):
    bundled = root / "bundled_config"
    bundled.mkdir(parents=True)
    manager = AccountManager(root / "data", bundled, lambda _path: [], (), ())
    manager.initialize()
    return manager


def test_history_export_uses_live_database_backup_and_excludes_migration_copies(tmp_path):
    source = _manager(tmp_path / "source")
    account_id = source.create_account("历史测试账号")
    account_root = source.account_dir(account_id)
    backup_root = account_root / ".migration-backups"
    backup_root.mkdir()
    (backup_root / "previous.sqlite3").write_bytes(b"excluded migration backup")
    (account_root / "logs" / "runtime.log").write_text("excluded log", encoding="utf-8")
    payload = history_payload(quantity=30)
    with UserDataDao(account_root / "user_data.sqlite3") as dao:
        dao.save_cultivation_history(account_id, "history-entry", payload)
        archive = export_account_data(source, account_id, tmp_path / "account.zip")
    extracted = tmp_path / "exported.sqlite3"
    with zipfile.ZipFile(archive) as exported:
        names = exported.namelist()
        assert "account/user_data.sqlite3" in names
        assert not any(".migration-backups" in name or "/logs/" in name for name in names)
        assert not any(name.endswith(("-wal", "-shm")) for name in names)
        extracted.write_bytes(exported.read("account/user_data.sqlite3"))
    with UserDataDao(extracted) as dao:
        assert dao.get_cultivation_history(account_id, "history-entry").payload == payload

    target = _manager(tmp_path / "target")
    # 同正式 ID 即同一账号；显示名变化不派生新身份或割断历史记录归属。
    index = target.read_index()
    index["accounts"].append({"id": account_id, "name": "另一个账号"})
    target.write_index(index)
    target.seed_account_data(account_id)
    untouched = (target.account_dir("default") / "user_data.sqlite3").read_bytes()
    imported_id = import_account_data(target, archive)
    assert imported_id == account_id
    with UserDataDao(target.account_dir(imported_id) / "user_data.sqlite3") as dao:
        assert dao.profile()["account_id"] == imported_id
        assert dao.get_cultivation_history(imported_id, "history-entry").payload == payload
    assert (target.account_dir("default") / "user_data.sqlite3").read_bytes() == untouched
