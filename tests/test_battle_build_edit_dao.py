# 验证战报角色配置只保留原始快照和一个可覆盖的修改副本。
from __future__ import annotations

import math
import json
import tempfile
import unittest
from pathlib import Path

from src.domain.battle_report_transfer import battle_equipment_sha256
from src.storage.sqlite.user_data_dao import UserDataDao


class BattleBuildEditDaoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "user.sqlite3"
        self.dao = UserDataDao(
            self.database,
            account_id="test-account",
            account_name="test-account",
        )

    def tearDown(self) -> None:
        self.dao.close()
        self.temp.cleanup()

    def _insert_imported_build(self) -> int:
        connection = self.dao._db()
        connection.executescript(
            """
            INSERT INTO battle_record(
                capture_operation_id, source_kind, capability_level,
                combat_context_kind, has_first_half, has_second_half,
                captured_at_utc, finalized_at_utc, dps_time_mode,
                duration_seconds, total_damage, total_dps,
                total_damage_taken, total_hits, character_count, skill_count,
                character_ids_json, abyss_detected, abyss_success,
                payload_schema_version, raw_summary_json,
                raw_summary_sha256, created_at_utc
            ) VALUES (
                'imported-operation', 'nte_core_summary', 'hit_axis',
                'non_abyss', 0, 0, 'now', 'now', 'active', 1, 1, 1,
                0, 1, 1, 0, '[1004]', 0, 0, 1, '{}',
                '0000000000000000000000000000000000000000000000000000000000000000',
                'now'
            );
            INSERT INTO battle_build_snapshot(
                battle_record_id, account_generation, profile_schema_version,
                observed_character_count, materialized_at_utc
            ) VALUES (last_insert_rowid(), 1, 1, 1, 'now');
            """
        )
        record_id = int(
            connection.execute(
                "SELECT battle_record_id FROM battle_record "
                "WHERE capture_operation_id = 'imported-operation'"
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO battle_character_build_snapshot(
                battle_record_id, character_id, profile_source,
                character_level, breakthrough_stage, awakening_level,
                ordinal, raw_profile_json
            ) VALUES (?, 1004, 'imported_bundle', 80, 6, 0, 0, '{}')
            """,
            (record_id,),
        )
        return record_id

    def test_one_edit_copy_can_be_overwritten_and_deactivated(self) -> None:
        connection = self.dao._db()
        connection.execute(
            """
            INSERT INTO battle_record(
                capture_operation_id, source_kind, capability_level,
                combat_context_kind, has_first_half, has_second_half,
                captured_at_utc, finalized_at_utc, dps_time_mode,
                duration_seconds, total_damage, total_dps,
                total_damage_taken, total_hits, character_count, skill_count,
                character_ids_json, abyss_detected, abyss_success,
                payload_schema_version, raw_summary_json,
                raw_summary_sha256, created_at_utc
            ) VALUES (
                'operation', 'nte_core_summary', 'summary_only', 'non_abyss', 0, 0,
                'now', 'now', 'active', 1, 1, 1, 0, 1, 1, 0,
                '[1004]', 0, 0, 1, '{}',
                '0000000000000000000000000000000000000000000000000000000000000000',
                'now'
            )
            """
        )
        record_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            """
            INSERT INTO battle_build_snapshot(
                battle_record_id, account_generation, profile_schema_version,
                observed_character_count, materialized_at_utc
            ) VALUES (?, 1, 1, 1, 'now')
            """,
            (record_id,),
        )
        connection.execute(
            """
            INSERT INTO battle_character_build_snapshot(
                battle_record_id, character_id, profile_source,
                character_level, breakthrough_stage, awakening_level,
                ordinal, raw_profile_json
            ) VALUES (?, 1004, 'official_graduation', 80, 6, 6, 0, '{}')
            """,
            (record_id,),
        )
        connection.commit()

        profile = {
            "character_id": 1004,
            "character_level": 80,
            "breakthrough_stage": 6,
            "selected_awaken_effect_ids": ["Effect1", "Effect3"],
            "likeability_level_10_enabled": True,
            "fork_id": "fork_Rose",
            "fork_level": 80,
            "fork_refinement_level": 1,
            "selected_skill_id": None,
            "skill_levels": {"melee": 10},
            "ordinal": 0,
            "equipment_context_key": "current",
            "equipment_context_title": "游戏当前",
            "equipment_source_kind": "role_page_current",
            "equipment_override": [
                {
                    "kind": "core",
                    "item_id": "Core_Test",
                    "uid_slot": 7,
                    "uid_serial": 11,
                    "quality": "orange",
                    "level": 80,
                    "max_level": 80,
                    "stats": [
                        {
                            "stat_group": "main",
                            "property_id": "AtkAdd",
                            "value": 777.0,
                            "is_percent": False,
                        }
                    ],
                }
            ],
        }
        self.dao.save_battle_build_edit(record_id, [profile])
        profile["skill_levels"] = {"melee": 8}
        self.dao.save_battle_build_edit(record_id, [profile])
        loaded = self.dao.load_battle_build_edit(record_id)
        self.assertTrue(loaded["is_active"])
        self.assertEqual({"melee": 8}, loaded["characters"][0]["profile"]["skill_levels"])
        self.assertEqual(
            "游戏当前",
            loaded["characters"][0]["profile"]["equipment_context_title"],
        )
        self.assertEqual(
            777.0,
            loaded["characters"][0]["profile"]["equipment_override"][0]["stats"][0]["value"],
        )
        repaired = self.dao.repair_battle_build_edit_shape_profiles({
            1004: {
                "extra_shape_label": "Type-3",
                "extra_shape_buffs": {"AtkUp": 10.0},
                "extra_shape_source": "static_database",
            }
        })
        loaded = self.dao.load_battle_build_edit(record_id)
        repaired_profile = loaded["characters"][0]["profile"]
        self.assertEqual(1, repaired["updated_profile_count"])
        self.assertEqual("Type-3", repaired_profile["extra_shape_label"])
        self.assertEqual({"AtkUp": 10.0}, repaired_profile["extra_shape_buffs"])
        self.assertEqual("static_database", repaired_profile["extra_shape_source"])

        self.dao.set_battle_build_edit_active(record_id, False)
        restored = self.dao.load_battle_build_edit(record_id)
        self.assertFalse(restored["is_active"])
        self.assertEqual({"melee": 8}, restored["characters"][0]["profile"]["skill_levels"])

    def test_equipment_override_rejects_virtual_or_duplicate_equipment(self) -> None:
        base = {
            "character_id": 1004,
            "character_level": 80,
            "breakthrough_stage": 6,
            "selected_awaken_effect_ids": [],
            "likeability_level_10_enabled": False,
            "fork_id": None,
            "skill_levels": {"melee": 10},
            "ordinal": 0,
        }
        duplicate = {
            **base,
            "equipment_override": [
                {"kind": "core", "item_id": "one", "uid_slot": 1, "uid_serial": 2},
                {"kind": "module", "item_id": "two", "uid_slot": 1, "uid_serial": 2},
            ],
        }
        with self.assertRaisesRegex(ValueError, "重复装备 UID"):
            self.dao._normalize_battle_build_edit_profile(duplicate)

        virtual = {
            **base,
            "equipment_override": [
                {"kind": "module", "item_id": "virtual", "uid_slot": 0, "uid_serial": 0}
            ],
        }
        with self.assertRaisesRegex(ValueError, "虚拟补位"):
            self.dao._normalize_battle_build_edit_profile(virtual)

    def test_manual_battle_stat_overrides_are_normalized_and_must_be_finite(self) -> None:
        profile = {
            "character_id": 1004,
            "character_level": 80,
            "breakthrough_stage": 6,
            "selected_awaken_effect_ids": [],
            "likeability_level_10_enabled": False,
            "fork_id": None,
            "skill_levels": {"melee": 10},
            "ordinal": 0,
            "battle_stat_overrides": {"AtkBase": "2039.5", "CritBase": 0.75},
        }

        normalized = self.dao._normalize_battle_build_edit_profile(profile)

        self.assertEqual(
            {"AtkBase": 2039.5, "CritBase": 0.75},
            normalized["battle_stat_overrides"],
        )
        for invalid in (math.nan, math.inf, "不是数字"):
            profile["battle_stat_overrides"] = {"AtkBase": invalid}
            with self.assertRaisesRegex(ValueError, "有限数值"):
                self.dao._normalize_battle_build_edit_profile(profile)

    def test_imported_battle_allows_cultivation_but_rejects_equipment_change(self) -> None:
        record_id = self._insert_imported_build()
        locked_equipment = [
            self.dao._normalize_equipment_override_item({
                "kind": "core",
                "item_id": "Core_Imported",
                "uid_slot": 7,
                "uid_serial": 11,
                "grid_count": 0,
                "stats": [],
            })
        ]
        connection = self.dao._db()
        connection.execute(
            """
            INSERT INTO battle_report_import_origin(
                battle_record_id, source_bundle_id, source_account_nickname,
                last_export_account_nickname, contract_version, imported_at_utc
            ) VALUES (?, 'bundle-1', '来源账号', '导出账号', 2, 'now')
            """,
            (record_id,),
        )
        connection.execute(
            """
            INSERT INTO battle_character_import_equipment_lock(
                battle_record_id, character_id, equipment_source_kind,
                equipment_sha256, locked_equipment_json, created_at_utc
            ) VALUES (?, 1004, 'frozen_battle_snapshot', ?, ?, 'now')
            """,
            (
                record_id,
                battle_equipment_sha256(locked_equipment),
                json.dumps(
                    locked_equipment,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )
        connection.commit()
        profile = {
            "character_id": 1004,
            "character_level": 70,
            "breakthrough_stage": 5,
            "selected_awaken_effect_ids": [],
            "likeability_level_10_enabled": False,
            "fork_id": None,
            "skill_levels": {"melee": 8},
            "ordinal": 0,
        }

        saved = self.dao.save_battle_build_edit(record_id, [profile])
        self.assertEqual(70, saved["characters"][0]["profile"]["character_level"])
        self.assertNotIn(
            "equipment_override",
            saved["characters"][0]["profile"],
        )

        changed = [{**locked_equipment[0], "item_id": "Core_Replaced"}]
        with self.assertRaisesRegex(ValueError, "不可修改"):
            self.dao.save_battle_build_edit(
                record_id,
                [{**profile, "equipment_override": changed}],
            )


if __name__ == "__main__":
    unittest.main()
