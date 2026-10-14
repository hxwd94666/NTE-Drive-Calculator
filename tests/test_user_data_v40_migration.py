# 验证用户数据库 v40 历史策略收口、迁移拒绝原子性与修复后重试。
"""真实 v39 账号偏好迁移的公共行为测试。"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from src.storage.sqlite.user_data_dao import SCHEMA_VERSION, UserDataDao, UserDataError
from tests.user_data_migration_helpers import (
    create_user_database_at_version,
    insert_optimization_profile,
)


def _create_legacy_preference(database: Path) -> None:
    # 从发布脚本构造真实 v39，不用删除当前库的单个版本标记伪造历史状态。
    create_user_database_at_version(database, 39)
    insert_optimization_profile(database, name="历史配置")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE optimization_preference_version SET allocation_strategy = 'global_optimal'"
        )


def test_v40_migrates_historical_global_priority_to_role_priority(tmp_path: Path) -> None:
    database = tmp_path / "legacy_v39.sqlite3"
    _create_legacy_preference(database)

    with UserDataDao(database) as migrated:
        assert migrated.summary()["schema_version"] == SCHEMA_VERSION
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT allocation_strategy FROM optimization_preference_version"
        ).fetchone()[0] == "role_priority"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_rejected_v40_preserves_old_facts_until_reference_repaired(tmp_path: Path) -> None:
    database = tmp_path / "damaged_v39.sqlite3"
    _create_legacy_preference(database)
    # 显式制造外部写入留下的旧库孤儿；发布迁移脚本不变，只验证拒绝时的事务边界。
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("UPDATE optimization_preference_version SET profile_id = 999")
    with closing(sqlite3.connect(database)) as connection:
        before = tuple(connection.iterdump())
        assert connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 39
        assert len(connection.execute("PRAGMA foreign_key_check").fetchall()) == 1

    # 首次失败与未修复重试都保留 schema、策略、数据和版本标记，避免第二次绕过校验。
    for _ in range(2):
        with pytest.raises(UserDataError, match="迁移 v40.*外键错误"):
            with UserDataDao(database):
                pass
        with closing(sqlite3.connect(database)) as connection:
            assert tuple(connection.iterdump()) == before

    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            """UPDATE optimization_preference_version
               SET profile_id = (SELECT profile_id FROM optimization_preference_profile)"""
        )
    with UserDataDao(database) as retried:
        assert retried.summary()["schema_version"] == SCHEMA_VERSION
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT allocation_strategy FROM optimization_preference_version"
        ).fetchone()[0] == "role_priority"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
