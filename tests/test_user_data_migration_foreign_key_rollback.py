# 验证用户数据库迁移产生外键违规时不提交、不推进版本且可重试。
"""迁移外键校验必须发生在提交之前：违规时回滚并保留可重试状态。

根因（2026-09-28 定位）：`user_data_base._migrate_schema` 原先在 `commit()` 之后
才执行 `PRAGMA foreign_key_check`，违规时事务已落库、`schema_migration` 已写入
目标版本，异常抛出后既无法回滚也会被后续启动跳过，违规数据永久留存。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.storage.sqlite import user_data_base
from src.storage.sqlite.user_data_dao import (
    SCHEMA_VERSION,
    USER_MIGRATIONS,
    UserDataDao,
    UserDataError,
)
from tests.user_data_migration_helpers import create_user_database_at_version

_VIOLATION_MIGRATION = """-- requires-foreign-keys-off
INSERT INTO loadout_plan_item(
    plan_id, ordinal, uid_serial, uid_slot, kind, raw_assignment_json
) VALUES (999999, 0, 1, 1, 'module', '{}');
"""


class UserDataMigrationForeignKeyRollbackTests(unittest.TestCase):
    def test_foreign_key_violation_rolls_back_and_can_retry(self) -> None:
        target = SCHEMA_VERSION + 1
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "user_data.sqlite3"
            create_user_database_at_version(database, SCHEMA_VERSION)
            script = root / f"v{target}_foreign_key_violation.sql"
            script.write_text(_VIOLATION_MIGRATION, encoding="utf-8")
            original = USER_MIGRATIONS.get(target)
            USER_MIGRATIONS[target] = script
            try:
                for attempt in range(2):
                    with patch.object(user_data_base, "SCHEMA_VERSION", target):
                        with self.assertRaises(UserDataError) as raised:
                            UserDataDao(database, account_id="migration-account")
                    self.assertIn("外键", str(raised.exception))

                    connection = sqlite3.connect(database)
                    try:
                        version = connection.execute(
                            "SELECT MAX(version) FROM schema_migration"
                        ).fetchone()[0]
                        orphaned = connection.execute(
                            "SELECT COUNT(*) FROM loadout_plan_item WHERE plan_id = 999999"
                        ).fetchone()[0]
                    finally:
                        connection.close()

                    self.assertEqual(
                        SCHEMA_VERSION, version,
                        f"第 {attempt + 1} 次尝试：迁移违规不得推进结构版本",
                    )
                    self.assertEqual(
                        0, orphaned,
                        f"第 {attempt + 1} 次尝试：迁移违规不得留下孤儿行",
                    )
            finally:
                if original is None:
                    USER_MIGRATIONS.pop(target, None)
                else:
                    USER_MIGRATIONS[target] = original


if __name__ == "__main__":
    unittest.main()
