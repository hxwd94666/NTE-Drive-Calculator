# 验证养成历史追加迁移的升级备份、事务回滚和重试。
from __future__ import annotations

import sqlite3

import pytest

from src.storage.sqlite.user_data_base import UserDataDaoCore
from src.storage.sqlite.user_data_dao import SCHEMA_VERSION, UserDataDao


NTE_TEST_TIER = "core"


def _legacy_database(path):
    with UserDataDao(path, account_id="migration-account"):
        pass
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE cultivation_history")
        connection.execute("DELETE FROM schema_migration WHERE version >= 45")


def test_existing_v44_backup_and_history_migration(tmp_path):
    database = tmp_path / "user_data.sqlite3"
    _legacy_database(database)
    with UserDataDao(database) as dao:
        assert dao.profile()["account_id"] == "migration-account"
        assert dao.list_cultivation_histories("migration-account").total == 0
    backups = list((tmp_path / ".migration-backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as old:
        assert old.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 44
        assert old.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with sqlite3.connect(database) as current:
        assert current.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == SCHEMA_VERSION


def test_history_migration_failure_rolls_back_and_can_retry(tmp_path, monkeypatch):
    database = tmp_path / "user_data.sqlite3"
    _legacy_database(database)
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
