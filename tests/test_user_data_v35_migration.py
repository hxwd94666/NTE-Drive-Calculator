"""用户数据库 v35 历史默认值兼容迁移的公共行为测试。"""

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


class UserDataV35MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "legacy_v34.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_v34_with_virtual_normal_resistance(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.executescript(
            """
            CREATE TABLE schema_migration (
                version INTEGER PRIMARY KEY,
                applied_at_utc TEXT NOT NULL
            );
            CREATE TABLE battle_target_condition (
                battle_record_id INTEGER PRIMARY KEY,
                resistance_chaos REAL NOT NULL
            );
            INSERT INTO battle_target_condition(
                battle_record_id, resistance_chaos
            ) VALUES (1, 0.1);
            """
        )
        v32_path = Path(__file__).resolve().parents[1] / (
            "src/storage/sqlite/schema/033_user_data_v32.sql"
        )
        connection.executescript(v32_path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migration(version, applied_at_utc) VALUES (34, 'now')"
        )
        connection.commit()
        self.assertEqual(
            0.2,
            connection.execute(
                "SELECT resistance_normal FROM battle_target_condition"
            ).fetchone()[0],
        )
        connection.close()

    def test_v35_materializes_virtual_default_before_adding_profile_snapshot(self) -> None:
        self._create_v34_with_virtual_normal_resistance()

        with UserDataDao(self.database) as migrated:
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
            row = migrated._db().execute(
                """
                SELECT resistance_normal, selected_target_profiles_json
                FROM battle_target_condition
                """
            ).fetchone()
            self.assertEqual(0.2, row["resistance_normal"])
            self.assertEqual("[]", row["selected_target_profiles_json"])
            self.assertEqual(
                ["ok"],
                [item[0] for item in migrated._db().execute("PRAGMA quick_check")],
            )

    def test_failed_v35_rolls_back_repair_and_can_retry(self) -> None:
        self._create_v34_with_virtual_normal_resistance()
        invalid_migration = Path(self.temp_dir.name) / "invalid_v35.sql"
        invalid_migration.write_text(
            """
            ALTER TABLE battle_target_condition
                ADD COLUMN selected_target_profiles_json TEXT NOT NULL DEFAULT '[]';
            this is deliberately invalid SQL;
            """,
            encoding="utf-8",
        )
        original_migration = USER_MIGRATIONS[35]
        USER_MIGRATIONS[35] = invalid_migration
        try:
            with self.assertRaises(UserDataError):
                UserDataDao(self.database)
        finally:
            USER_MIGRATIONS[35] = original_migration

        connection = sqlite3.connect(self.database)
        self.assertEqual(
            34,
            connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0],
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(battle_target_condition)")
        }
        self.assertNotIn("selected_target_profiles_json", columns)
        connection.close()

        with UserDataDao(self.database) as retried:
            self.assertEqual(SCHEMA_VERSION, retried.summary()["schema_version"])
            self.assertEqual(
                ["ok"],
                [item[0] for item in retried._db().execute("PRAGMA quick_check")],
            )


if __name__ == "__main__":
    unittest.main()
