# 验证用户数据库 v35 历史默认值兼容迁移。
"""用户数据库 v35 历史默认值兼容迁移的公共行为测试。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.storage.sqlite.user_data_dao import (
    SCHEMA_VERSION,
    USER_MIGRATIONS,
    UserDataDao,
    UserDataError,
)
from tests.user_data_migration_helpers import (
    create_user_database_at_version,
    migrate_user_database_to_version,
)


class UserDataV35MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "legacy_v34.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_v34_with_virtual_normal_resistance(self) -> None:
        create_user_database_at_version(self.database, 31)
        connection = sqlite3.connect(self.database)
        connection.execute(
            """INSERT INTO battle_record(
                   battle_record_id, capture_operation_id, source_kind,
                   capability_level, combat_context_kind, abyss_floor,
                   has_first_half, has_second_half, captured_at_utc,
                   finalized_at_utc, dps_time_mode, duration_seconds,
                   total_damage, total_dps, total_damage_taken, total_hits,
                   character_count, skill_count, character_ids_json,
                   abyss_detected, abyss_success, payload_schema_version,
                   raw_summary_json, raw_summary_sha256, created_at_utc
               ) VALUES (
                   1, 'migration-v35', 'nte_core_summary', 'summary_only',
                   'non_abyss', NULL, 1, 0, 'now', 'now', 'active',
                   1, 0, 0, 0, 0, 0, 0, '[]', 0, 0, 1, '{}',
                   '0000000000000000000000000000000000000000000000000000000000000000',
                   'now'
               )"""
        )
        connection.execute(
            """INSERT INTO battle_target_condition(
                   battle_record_id, target_name, enemy_level, scene,
                   defense_reduction, vulnerability,
                   resistance_chaos, resistance_cosmos, resistance_incantation,
                   resistance_lakshana, resistance_nature, resistance_psyche,
                   resistance_psychically, updated_at_utc
               ) VALUES (
                   1, '迁移目标', 80, 'open_world', 0, 0,
                   0.1, 0, 0, 0, 0, 0, 0, 'now'
               )"""
        )
        connection.commit()
        connection.close()

        migrate_user_database_to_version(self.database, 34)
        connection = sqlite3.connect(self.database)
        self.assertEqual(
            0.2,
            connection.execute(
                "SELECT resistance_normal FROM battle_target_condition"
            ).fetchone()[0],
        )
        connection.close()

    def _assert_verified_history_backup(self) -> None:
        backups = list((self.database.parent / ".migration-backups").glob("*.sqlite3"))
        self.assertEqual(1, len(backups))
        with closing(sqlite3.connect(backups[0])) as backup:
            # 已发布旧迁移先完成修复；完整性备份仍先于养成历史 v45，而不是跳过门禁。
            self.assertEqual(44, backup.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0])
            self.assertEqual([("ok",)], backup.execute("PRAGMA quick_check").fetchall())
            self.assertIsNone(backup.execute(
                "SELECT name FROM sqlite_master WHERE name = 'cultivation_history'"
            ).fetchone())

    def test_v35_materializes_virtual_default_before_adding_profile_snapshot(self) -> None:
        self._create_v34_with_virtual_normal_resistance()

        with UserDataDao(self.database) as migrated:
            self.assertEqual(
                SCHEMA_VERSION,
                migrated._db().execute(
                    "SELECT MAX(version) FROM schema_migration"
                ).fetchone()[0],
            )
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
        self._assert_verified_history_backup()

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
            with self.assertRaises(UserDataError) as failed:
                UserDataDao(self.database)
        finally:
            USER_MIGRATIONS[35] = original_migration
        # 必须抵达注入的 v35 SQL 失败，不能因更早的备份失败而虚假通过回滚断言。
        self.assertIsInstance(failed.exception.__cause__, sqlite3.OperationalError)
        self.assertIn("syntax error", str(failed.exception.__cause__))
        self.assertFalse((self.database.parent / ".migration-backups").exists())

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
            self.assertEqual(
                SCHEMA_VERSION,
                retried._db().execute(
                    "SELECT MAX(version) FROM schema_migration"
                ).fetchone()[0],
            )
            self.assertEqual(
                ["ok"],
                [item[0] for item in retried._db().execute("PRAGMA quick_check")],
            )
        self._assert_verified_history_backup()


if __name__ == "__main__":
    unittest.main()
