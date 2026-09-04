# 验证用户数据库 v38 战报自动目标推断快照迁移。
"""用户数据库 v38 战报自动目标推断快照迁移的公共行为测试。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import (
    SCHEMA_VERSION,
    USER_MIGRATIONS,
    UserDataDao,
    UserDataError,
)


class UserDataV38MigrationTests(unittest.TestCase):
    def test_new_database_has_versioned_inferred_target_snapshot_table(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "user_data.sqlite3"
            with UserDataDao(
                database,
                account_id="migration-account",
                account_name="迁移测试账号",
            ) as user_dao:
                columns = {
                    str(row[1])
                    for row in user_dao._db().execute(
                        "PRAGMA table_info(battle_inferred_target_snapshot)"
                    )
                }

                self.assertEqual(
                    {
                        "battle_record_id",
                        "payload_schema_version",
                        "algorithm_version",
                        "static_dataset_id",
                        "static_schema_version",
                        "inference_status",
                        "environment_kind",
                        "environment_ref",
                        "environment_name",
                        "source_kind",
                        "confidence",
                        "inferred_payload_json",
                        "updated_at_utc",
                    },
                    columns,
                )
                self.assertEqual(SCHEMA_VERSION, user_dao.summary()["schema_version"])

    def test_v37_database_upgrades_without_rewriting_battle_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "legacy_v37.sqlite3"
            with UserDataDao(
                database,
                account_id="migration-account",
                account_name="迁移测试账号",
            ):
                pass
            connection = sqlite3.connect(database)
            connection.execute("DROP TABLE battle_inferred_target_snapshot")
            connection.execute(
                "ALTER TABLE battle_time_stop_interval DROP COLUMN pause_type_mask"
            )
            connection.execute("DELETE FROM schema_migration WHERE version >= 38")
            connection.commit()
            connection.close()

            with UserDataDao(
                database,
                account_id="migration-account",
                account_name="迁移测试账号",
            ) as migrated:
                table = migrated._db().execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table'
                      AND name = 'battle_inferred_target_snapshot'
                    """
                ).fetchone()
                self.assertIsNotNone(table)
                self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])

    def test_failed_v38_migration_rolls_back_and_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "retry_v37.sqlite3"
            with UserDataDao(
                database,
                account_id="migration-account",
                account_name="迁移测试账号",
            ):
                pass
            connection = sqlite3.connect(database)
            connection.execute("DROP TABLE battle_inferred_target_snapshot")
            connection.execute(
                "ALTER TABLE battle_time_stop_interval DROP COLUMN pause_type_mask"
            )
            connection.execute("DELETE FROM schema_migration WHERE version >= 38")
            connection.commit()
            connection.close()
            invalid = root / "invalid_v38.sql"
            invalid.write_text(
                "CREATE TABLE migration_should_rollback(id INTEGER); INVALID SQL;",
                encoding="utf-8",
            )
            original = USER_MIGRATIONS[38]
            USER_MIGRATIONS[38] = invalid
            try:
                with self.assertRaises(UserDataError):
                    UserDataDao(
                        database,
                        account_id="migration-account",
                        account_name="迁移测试账号",
                    )
            finally:
                USER_MIGRATIONS[38] = original

            connection = sqlite3.connect(database)
            maximum = connection.execute(
                "SELECT MAX(version) FROM schema_migration"
            ).fetchone()[0]
            rolled_back = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'migration_should_rollback'
                """
            ).fetchone()
            connection.close()
            self.assertEqual(37, maximum)
            self.assertIsNone(rolled_back)

            with UserDataDao(
                database,
                account_id="migration-account",
                account_name="迁移测试账号",
            ) as retried:
                self.assertEqual(SCHEMA_VERSION, retried.summary()["schema_version"])


if __name__ == "__main__":
    unittest.main()
