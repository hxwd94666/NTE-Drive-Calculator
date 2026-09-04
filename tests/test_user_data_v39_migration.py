# 验证用户数据库 v39 持久化可空的时停类型掩码。
"""用户数据库 v39 typed time-stop 迁移的公共行为测试。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import SCHEMA_VERSION, UserDataDao


class UserDataV39MigrationTests(unittest.TestCase):
    def test_new_database_has_nullable_time_stop_type_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "user_data.sqlite3"
            with UserDataDao(
                database,
                account_id="migration-account",
                account_name="迁移测试账号",
            ) as user_dao:
                columns = {
                    str(row[1]): row
                    for row in user_dao._db().execute(
                        "PRAGMA table_info(battle_time_stop_interval)"
                    )
                }

                self.assertIn("pause_type_mask", columns)
                self.assertEqual(0, int(columns["pause_type_mask"][3]))
                self.assertEqual(SCHEMA_VERSION, user_dao.summary()["schema_version"])

    def test_v38_database_adds_column_without_rewriting_raw_intervals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "legacy_v38.sqlite3"
            with UserDataDao(
                database,
                account_id="migration-account",
                account_name="迁移测试账号",
            ):
                pass
            connection = sqlite3.connect(database)
            connection.execute(
                "ALTER TABLE battle_time_stop_interval DROP COLUMN pause_type_mask"
            )
            connection.execute("DELETE FROM schema_migration WHERE version = 39")
            connection.commit()
            connection.close()

            with UserDataDao(database) as migrated:
                columns = {
                    str(row[1])
                    for row in migrated._db().execute(
                        "PRAGMA table_info(battle_time_stop_interval)"
                    )
                }
                self.assertIn("pause_type_mask", columns)
                self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])


if __name__ == "__main__":
    unittest.main()
