# 验证账号战报 DAO、迁移和保留上限。
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.storage.sqlite import user_data_base
from src.storage.sqlite.user_data_dao import (
    BATTLE_REPORT_MAX_MANUAL_RECORDS,
    BATTLE_REPORT_MAX_RECORDS,
    SCHEMA_VERSION,
    UserDataDao,
    UserDataValidationError,
)


class BattleReportDaoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "user_data.sqlite3"
        self.dao = UserDataDao(
            self.database_path,
            account_id="battle-account",
            account_name="战报测试账号",
        )

    def tearDown(self) -> None:
        self.dao.close()
        self.temporary.cleanup()

    def _insert(self, index: int, *, abyss: bool = True) -> dict:
        payload = {
            "abyss": {
                "detected": abyss,
                "floor": 8 if abyss else None,
                "success": abyss,
            },
            "characters": [{"char_id": 1000 + index}],
            "total_damage": float(index + 1),
            "total_hits": index + 1,
        }
        raw_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return self.dao.insert_auto_summary_snapshot(
            capture_operation_id=f"battle-operation-{index}",
            combat_context_kind="abyss" if abyss else "non_abyss",
            abyss_floor=8 if abyss else None,
            has_first_half=abyss,
            has_second_half=abyss,
            captured_at_utc=f"2026-08-07T00:00:00.{index:03d}+00:00",
            finalized_at_utc=f"2026-08-07T00:00:01.{index:03d}+00:00",
            dps_time_mode="subtract_time_stop",
            duration_seconds=10.0,
            total_damage=float(index + 1),
            total_dps=float(index + 1) / 10.0,
            total_damage_taken=0.0,
            total_hits=index + 1,
            character_count=1,
            skill_count=1,
            character_ids=[1000 + index],
            abyss_detected=abyss,
            abyss_success=abyss,
            payload_schema_version=1,
            raw_summary_json=raw_json,
            raw_summary_sha256=hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
        )

    def test_v13_schema_persists_raw_summary_and_restores_last_record(self) -> None:
        result = self._insert(1, abyss=False)
        record_id = int(result["record"]["battle_record_id"])

        restored = self.dao.restore_battle_report_record()

        self.assertTrue(result["inserted"])
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(record_id, restored["battle_record_id"])
        self.assertEqual("non_abyss", restored["combat_context_kind"])
        self.assertIsNone(restored["abyss_floor"])
        self.assertEqual("auto", restored["retention_kind"])
        self.assertEqual(1001, restored["character_ids"][0])
        self.assertEqual(2.0, restored["raw_summary_payload"]["total_damage"])

    def test_capture_operation_is_idempotent_and_rejects_changed_payload(self) -> None:
        first = self._insert(2)
        second = self._insert(2)

        self.assertTrue(first["inserted"])
        self.assertFalse(second["inserted"])
        self.assertEqual(
            first["record"]["battle_record_id"],
            second["record"]["battle_record_id"],
        )
        self.assertEqual(1, len(self.dao.list_battle_records()))

        payload = {"total_damage": 999.0}
        raw_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with self.assertRaisesRegex(UserDataValidationError, "不同战报 payload"):
            self.dao.insert_auto_summary_snapshot(
                capture_operation_id="battle-operation-2",
                combat_context_kind="non_abyss",
                abyss_floor=None,
                has_first_half=False,
                has_second_half=False,
                captured_at_utc="2026-08-07T00:00:00+00:00",
                finalized_at_utc="2026-08-07T00:00:01+00:00",
                dps_time_mode="subtract_time_stop",
                duration_seconds=1.0,
                total_damage=999.0,
                total_dps=999.0,
                total_damage_taken=0.0,
                total_hits=1,
                character_count=0,
                skill_count=0,
                character_ids=[],
                abyss_detected=False,
                abyss_success=False,
                payload_schema_version=1,
                raw_summary_json=raw_json,
                raw_summary_sha256=hashlib.sha256(
                    raw_json.encode("utf-8")
                ).hexdigest(),
            )

    def test_101st_record_prunes_oldest_auto_and_preserves_manual_records(self) -> None:
        record_ids = [
            int(self._insert(index)["record"]["battle_record_id"])
            for index in range(BATTLE_REPORT_MAX_RECORDS)
        ]
        for record_id in record_ids[:BATTLE_REPORT_MAX_MANUAL_RECORDS]:
            self.dao.promote_battle_record_to_manual(record_id)

        inserted = self._insert(BATTLE_REPORT_MAX_RECORDS)
        records = self.dao.list_battle_records()
        remaining_ids = {int(record["battle_record_id"]) for record in records}

        self.assertEqual(BATTLE_REPORT_MAX_RECORDS, len(records))
        self.assertTrue(set(record_ids[:50]).issubset(remaining_ids))
        self.assertNotIn(record_ids[50], remaining_ids)
        self.assertEqual(
            (record_ids[50],),
            inserted["pruned_battle_record_ids"],
        )

    def test_51st_manual_save_prunes_oldest_manual_record(self) -> None:
        record_ids = [
            int(self._insert(index)["record"]["battle_record_id"])
            for index in range(BATTLE_REPORT_MAX_MANUAL_RECORDS + 1)
        ]
        final_result: dict | None = None
        for record_id in record_ids:
            final_result = self.dao.promote_battle_record_to_manual(record_id)

        records = self.dao.list_battle_records()
        assert final_result is not None
        self.assertEqual(BATTLE_REPORT_MAX_MANUAL_RECORDS, len(records))
        self.assertTrue(all(row["retention_kind"] == "manual" for row in records))
        self.assertEqual(
            (record_ids[0],),
            final_result["pruned_battle_record_ids"],
        )
        self.assertIsNone(self.dao.load_battle_record(record_ids[0]))

    def test_delete_selected_record_falls_back_to_latest_history(self) -> None:
        first_id = int(self._insert(1)["record"]["battle_record_id"])
        second_id = int(self._insert(2)["record"]["battle_record_id"])
        self.dao.update_battle_report_page_state(
            battle_record_id=first_id,
            detail_scope="first",
        )

        self.assertTrue(self.dao.delete_battle_record(first_id))
        restored = self.dao.restore_battle_report_record()

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(second_id, restored["battle_record_id"])
        self.assertEqual("current", restored["restored_detail_scope"])

    def test_history_retention_action_does_not_change_current_view_pointer(self) -> None:
        first_id = int(self._insert(10)["record"]["battle_record_id"])
        second_id = int(self._insert(11)["record"]["battle_record_id"])
        self.dao.update_battle_report_page_state(
            battle_record_id=first_id,
            detail_scope="first",
        )

        self.dao.promote_battle_record_to_manual(second_id)
        restored = self.dao.restore_battle_report_record()

        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertEqual(first_id, restored["battle_record_id"])
        self.assertEqual("first", restored["restored_detail_scope"])

    def test_analysis_range_is_restored_without_overwriting_detail_scope(self) -> None:
        record_id = int(self._insert(12)["record"]["battle_record_id"])
        self.dao.update_battle_report_page_state(
            battle_record_id=record_id,
            detail_scope="second",
        )
        self.dao.update_battle_report_analysis_state(
            battle_record_id=record_id,
            start_us=1_250_000,
            end_us=7_500_000,
            character_id=1072,
        )

        restored = self.dao.restore_battle_report_record()

        assert restored is not None
        self.assertEqual("second", restored["restored_detail_scope"])
        self.assertEqual(1_250_000, restored["restored_analysis_start_us"])
        self.assertEqual(7_500_000, restored["restored_analysis_end_us"])
        self.assertEqual(1072, restored["restored_analysis_character_id"])

    def test_existing_v12_database_migrates_to_battle_report_tables(self) -> None:
        legacy_path = Path(self.temporary.name) / "legacy_v12.sqlite3"
        with patch.object(user_data_base, "SCHEMA_VERSION", 12):
            with UserDataDao(
                legacy_path,
                account_id="legacy",
                account_name="旧账号",
            ) as legacy:
                version = legacy._db().execute(
                    "SELECT MAX(version) FROM schema_migration"
                ).fetchone()[0]
                self.assertEqual(12, version)

        with UserDataDao(legacy_path) as migrated:
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
            tables = {
                str(row[0])
                for row in migrated._db().execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name LIKE 'battle_%'
                    """
                )
            }
        self.assertEqual(
            {
                "battle_axis_capture",
                "battle_build_snapshot",
                "battle_build_edit",
                "battle_character_awaken_edit",
                "battle_character_build_edit",
                "battle_character_build_snapshot",
                "battle_character_import_equipment_lock",
                "battle_character_skill_edit",
                "battle_character_skill_snapshot",
                "battle_character_stat_snapshot",
                "battle_equipment_snapshot",
                "battle_equipment_stat_snapshot",
                "battle_hit_evidence",
                "battle_record",
                "battle_record_retention",
                "battle_report_import_origin",
                "battle_report_page_state",
                "battle_time_stop_interval",
                "battle_target_condition",
            },
            tables,
        )

    def test_existing_v26_database_migrates_target_condition_table(self) -> None:
        legacy_path = Path(self.temporary.name) / "legacy_v26.sqlite3"
        with patch.object(user_data_base, "SCHEMA_VERSION", 26):
            with UserDataDao(
                legacy_path,
                account_id="legacy",
                account_name="旧账号",
            ) as legacy:
                self.assertEqual(26, legacy.summary()["schema_version"])

        with UserDataDao(legacy_path) as migrated:
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
            table = migrated._db().execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'battle_target_condition'"
            ).fetchone()

        self.assertIsNotNone(table)

    def test_existing_v29_database_migrates_formula_source_groups(self) -> None:
        legacy_path = Path(self.temporary.name) / "legacy_v29.sqlite3"
        with patch.object(user_data_base, "SCHEMA_VERSION", 29):
            with UserDataDao(
                legacy_path,
                account_id="legacy",
                account_name="旧账号",
            ) as legacy:
                self.assertEqual(29, legacy.summary()["schema_version"])

        with UserDataDao(legacy_path) as migrated:
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
            table_sql = migrated._db().execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'battle_character_stat_snapshot'"
            ).fetchone()[0]

        self.assertIn("'character'", table_sql)
        self.assertIn("'fork'", table_sql)
        self.assertIn("'likeability'", table_sql)
        self.assertIn("'world_bonus'", table_sql)

    def test_existing_v31_database_adds_normal_target_resistance(self) -> None:
        legacy_path = Path(self.temporary.name) / "legacy_v31.sqlite3"
        with patch.object(user_data_base, "SCHEMA_VERSION", 31):
            with UserDataDao(
                legacy_path,
                account_id="legacy",
                account_name="旧账号",
            ) as legacy:
                self.assertEqual(31, legacy.summary()["schema_version"])

        with UserDataDao(legacy_path) as migrated:
            columns = {
                row[1]: row[4]
                for row in migrated._db().execute(
                    "PRAGMA table_info(battle_target_condition)"
                )
            }

        self.assertEqual("0.2", columns["resistance_normal"])

    def test_existing_v32_database_adds_transfer_locks_and_v4_max_hp(self) -> None:
        legacy_path = Path(self.temporary.name) / "legacy_v32.sqlite3"
        with patch.object(user_data_base, "SCHEMA_VERSION", 32):
            with UserDataDao(
                legacy_path,
                account_id="legacy",
                account_name="旧账号",
            ) as legacy:
                self.assertEqual(32, legacy.summary()["schema_version"])

        with UserDataDao(legacy_path) as migrated:
            connection = migrated._db()
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            hit_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(battle_hit_evidence)"
                )
            }
            capture_columns = {
                row[1] for row in connection.execute(
                    "PRAGMA table_info(battle_axis_capture)"
                )
            }

        self.assertIn("battle_report_import_origin", tables)
        self.assertIn("battle_character_import_equipment_lock", tables)
        self.assertIn("max_hp_reduction", hit_columns)
        self.assertIn("finalization_incomplete_reason", capture_columns)

    def test_existing_v34_database_adds_frozen_target_profiles(self) -> None:
        legacy_path = Path(self.temporary.name) / "legacy_v34.sqlite3"
        with patch.object(user_data_base, "SCHEMA_VERSION", 34):
            with UserDataDao(
                legacy_path,
                account_id="legacy",
                account_name="旧账号",
            ) as legacy:
                self.assertEqual(34, legacy.summary()["schema_version"])

        with UserDataDao(legacy_path) as migrated:
            columns = {
                row[1]: row[4]
                for row in migrated._db().execute(
                    "PRAGMA table_info(battle_target_condition)"
                )
            }

        self.assertEqual("'[]'", columns["selected_target_profiles_json"])


if __name__ == "__main__":
    unittest.main()
