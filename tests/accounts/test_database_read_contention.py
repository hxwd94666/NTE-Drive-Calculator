# 验证结构读取锁冲突保留真实原因、释放连接并允许原账号原样重试。
from __future__ import annotations

import sqlite3

import pytest

from src.storage.sqlite import user_data_base
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError

NTE_TEST_TIER = "core"


def test_real_read_lock_reports_busy_then_reopens_original_profile(tmp_path, monkeypatch):
    database = tmp_path / "account.sqlite3"
    with UserDataDao(database, account_id="synthetic-account") as dao:
        before = dao.profile()
    real_connect = sqlite3.connect
    opened, closed = [], []

    class FastConnection(sqlite3.Connection):
        # 只压缩测试等待预算，SQL 与错误码仍由真实 SQLite 锁产生。
        def execute(self, sql, parameters=(), /):
            if sql.strip().upper().startswith("PRAGMA BUSY_TIMEOUT"):
                return super().execute("PRAGMA busy_timeout = 20")
            return super().execute(sql, parameters)

        def close(self):
            super().close()
            closed.append(self)

    def connect(*args, **kwargs):
        connection = real_connect(*args, factory=FastConnection, **kwargs)
        opened.append(connection)
        return connection

    holder = real_connect(database)
    holder.execute("PRAGMA journal_mode=DELETE").fetchone()
    holder.execute("BEGIN EXCLUSIVE")
    monkeypatch.setattr(user_data_base.sqlite3, "connect", connect)
    try:
        with pytest.raises(UserDataError, match="正忙") as caught:
            UserDataDao(database)
        assert isinstance(caught.value.__cause__, sqlite3.Error)
        assert caught.value.__cause__.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
        assert opened and set(closed) == set(opened)
    finally:
        holder.rollback()
        holder.close()
    with UserDataDao(database) as dao:
        assert dao.profile() == before


def test_foreign_sqlite_file_remains_a_schema_identity_failure(tmp_path):
    database = tmp_path / "foreign.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE unrelated(value)")
    connection.close()
    with pytest.raises(UserDataError, match="文件不是 NTE 用户数据库"):
        UserDataDao(database)
