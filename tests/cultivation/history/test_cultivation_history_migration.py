# 验证养成历史追加迁移的升级备份、事务回滚和重试。
from __future__ import annotations

import sqlite3

import pytest

from src.storage.sqlite.user_data_base import UserDataDaoCore
from src.storage.sqlite.user_data_dao import SCHEMA_VERSION, UserDataDao


NTE_TEST_TIER = "core"


def _legacy_database(path, monkeypatch):
    from src.storage.sqlite import user_data_base
    # 真正建立 v44；删除新版版本号会残留后续表，不是可升级的旧库。
    with monkeypatch.context() as patch:
        patch.setattr(user_data_base, "SCHEMA_VERSION", 44)
        with UserDataDao(path, account_id="migration-account"):
            pass


def test_existing_v44_backup_and_history_migration(tmp_path, monkeypatch):
    database = tmp_path / "user_data.sqlite3"
    _legacy_database(database, monkeypatch)
    with UserDataDao(database) as dao:
        assert dao.profile()["account_id"] == "migration-account"
        assert dao.list_cultivation_histories("migration-account").total == 0
    backups = list((tmp_path / ".migration-backups").glob("*.sqlite3"))
    versions = set()
    for backup in backups:
        with sqlite3.connect(backup) as old:
            versions.add(old.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0])
            assert old.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # 养成历史和队伍简报各自升级前备份，完整保留两个可恢复边界。
    assert versions == {44, 46, 47, 48, 49, 50, 51, 52}
    with sqlite3.connect(database) as current:
        assert current.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == SCHEMA_VERSION


def test_history_migration_failure_rolls_back_and_can_retry(tmp_path, monkeypatch):
    database = tmp_path / "user_data.sqlite3"
    _legacy_database(database, monkeypatch)
    execute = UserDataDaoCore._execute_migration_script

    def fail(connection, script):
        execute(connection, script)
        raise sqlite3.OperationalError("injected migration failure")

    with monkeypatch.context() as context:
        context.setattr(UserDataDaoCore, "_execute_migration_script", staticmethod(fail))
        with pytest.raises(RuntimeError):
            UserDataDao(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 44
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'cultivation_history'").fetchone() is None
    with UserDataDao(database) as dao:
        assert dao.list_cultivation_histories("migration-account").total == 0


def test_new_database_does_not_backup_empty_base(tmp_path):
    with UserDataDao(tmp_path / "user_data.sqlite3", account_id="new"):
        pass
    assert not (tmp_path / ".migration-backups").exists()
