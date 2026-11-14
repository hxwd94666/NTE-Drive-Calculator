# 验证独立分析的只读WAL租约不迁移、不锁住写入且在异常后释放。
import hashlib
import sqlite3
from unittest.mock import patch

import pytest

from src.storage.sqlite.user_data_dao import UserDataDao


def test_read_lease_does_not_migrate_and_allows_concurrent_writer(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    with UserDataDao(path, account_id="read-lease"):
        pass
    with patch.object(UserDataDao, "_migrate_schema", side_effect=AssertionError("lease migrated database")):
        with UserDataDao.external_read_lease(path):
            # 不保持读事务；同一文件仍可由另一连接正常提交。
            writer = sqlite3.connect(path, timeout=0.1)
            try:
                with writer:
                    writer.execute("UPDATE database_profile SET account_name = ?", ("updated",))
            finally:
                writer.close()
    with UserDataDao(path) as dao:
        assert dao.profile()["account_name"] == "updated"


def test_read_lease_alone_never_rewrites_main_database_and_releases_on_error(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    with UserDataDao(path, account_id="read-lease"):
        pass
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    opened = []

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            return super().close()

    original = sqlite3.connect

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs, factory=TrackedConnection)
        opened.append(connection)
        return connection

    with patch("src.storage.sqlite.user_data_base.sqlite3.connect", side_effect=connect):
        with pytest.raises(RuntimeError, match="owner failed"):
            with UserDataDao.external_read_lease(path):
                assert not opened[0].in_transaction
                with pytest.raises(sqlite3.OperationalError):
                    opened[0].execute("UPDATE database_profile SET account_name='forbidden'")
                raise RuntimeError("owner failed")
    assert len(opened) == 1 and opened[0].closed
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_read_lease_does_not_create_missing_input(tmp_path):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError):
        with UserDataDao.external_read_lease(path):
            pytest.fail("missing input reached native task")
    assert not path.exists()
