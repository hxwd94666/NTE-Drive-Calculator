# 测试分账号用户数据库的初始化、快照和装配方案读写。
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import src.storage.sqlite.user_data_dao as user_data_dao_module
from src.storage.sqlite.user_data_dao import (
    UserDataDao,
    UserDataError,
    UserDataValidationError,
)


def stat(property_id: str, value: float, percent: bool = False) -> dict:
    return {
        "property_id": property_id,
        "value": value,
        "percent": percent,
        "names": {"zh_cn": property_id},
    }


def item(serial: int, slot: int, kind: str = "module") -> dict:
    return {
        "uid": {"serial": serial, "slot": slot},
        "kind": kind,
        "item_id": "cell3_style1_1_Orange" if kind == "module" else "Attack_orange",
        "suit_id": "Suit1",
        "geometry": "ZhiJiao1" if kind == "module" else "Core",
        "grid": 3 if kind == "module" else None,
        "quality": "orange",
        "level": 20,
        "max_level": 20,
        "locked": serial % 2 == 0,
        "discarded": False,
        "equipped": False,
        "equipped_character_uid": None,
        "equipped_character_id": None,
        "equipped_placement": None,
        "names": {"zh_cn": "测试驱动"},
        "suit_names": {"zh_cn": "测试空幕"},
        "main_stats": [stat("AtkUp", 0.1, True)],
        "sub_stats": [stat("CritBase", 0.03, True), stat("AtkAdd", 80.0)],
    }


def snapshot(generation: int, rows: list[dict]) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "event.inventory.snapshot",
        "params": {
            "complete": True,
            "generation": generation,
            "sequence": generation + 1,
            "observed_at_unix_ms": 1_784_308_856_895 + generation,
            "item_count": len(rows),
            "items": rows,
        },
    }


class UserDataDaoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "user_data.sqlite3"
        self.dao = UserDataDao(
            self.database, account_id="default", account_name="默认账号"
        )

    def tearDown(self) -> None:
        self.dao.close()
        self.temp_dir.cleanup()

    def test_initializes_profile_and_typed_sync_settings(self) -> None:
        summary = self.dao.summary()
        self.assertEqual(summary["schema_version"], 10)
        self.assertEqual(summary["profile"]["account_id"], "default")
        self.assertEqual(summary["sync_settings"]["inventory_sync_method"], "nte_core")
        self.assertEqual(summary["sync_settings"]["inventory_settle_seconds"], 5.0)
        self.assertFalse(summary["sync_settings"]["auto_start_inventory_sync"])
        self.assertEqual(
            20, summary["sync_settings"]["inventory_snapshot_retention_count"]
        )

        settings = self.dao.update_sync_settings(
            inventory_sync_method="gamepad",
            equipment_apply_method="nte_core",
            raw_capture_enabled=True,
            inventory_settle_seconds=8.5,
            auto_start_inventory_sync=True,
            inventory_snapshot_retention_count=7,
        )
        self.assertEqual(settings["inventory_sync_method"], "gamepad")
        self.assertTrue(settings["raw_capture_enabled"])
        self.assertTrue(settings["auto_start_inventory_sync"])
        self.assertEqual(7, settings["inventory_snapshot_retention_count"])
        with self.assertRaises(UserDataValidationError):
            self.dao.update_sync_settings(inventory_sync_method="legacy_json")
        with self.assertRaises(UserDataValidationError):
            self.dao.update_sync_settings(inventory_snapshot_retention_count=0)

    def test_migrates_existing_v1_database_without_losing_profile(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_v1.sqlite3"
        schema_path = Path(__file__).resolve().parents[1] / "src" / "storage" / "sqlite" / "schema" / "001_user_data.sql"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        connection.execute("INSERT INTO schema_migration VALUES (1, '2026-07-18')")
        connection.execute(
            "INSERT INTO database_profile VALUES (1, 'legacy', '旧账号', 'now', 'now')"
        )
        connection.execute(
            """
            INSERT INTO sync_settings(
                singleton_id, inventory_sync_method, equipment_apply_method,
                capture_device_id, raw_capture_enabled,
                inventory_settle_seconds, updated_at_utc
            ) VALUES (1, 'nte_core', 'nte_core', NULL, 0, 15.0, 'now')
            """
        )
        connection.commit()
        connection.close()

        with UserDataDao(legacy_path) as migrated:
            settings = migrated.get_sync_settings()
            self.assertEqual(10, migrated.summary()["schema_version"])
            self.assertEqual("旧账号", migrated.profile()["account_name"])
            self.assertEqual(5.0, settings["inventory_settle_seconds"])
            self.assertFalse(settings["auto_start_inventory_sync"])
            self.assertEqual(20, settings["inventory_snapshot_retention_count"])

    def test_migrates_v4_database_to_versioned_optimization_preferences(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_v4.sqlite3"
        with UserDataDao(legacy_path, account_id="legacy") as initialized:
            self.assertEqual(10, initialized.summary()["schema_version"])

        connection = sqlite3.connect(legacy_path)
        for table in (
            "character_shape_bonus_preference_property",
            "character_shape_bonus_preference",
            "ui_item_order",
            "application_setting_migration",
            "application_setting_copy",
            "character_weight_preference_property",
            "character_weight_preference_seed",
            "character_profile_skill",
            "character_profile",
            "optimization_preference_property_limit",
            "optimization_preference_substat_priority",
            "optimization_preference_property_weight",
            "optimization_preference_character",
            "optimization_preference_version",
            "optimization_preference_profile",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migration WHERE version >= 5")
        connection.commit()
        connection.close()

        with UserDataDao(legacy_path) as migrated:
            self.assertEqual(10, migrated.summary()["schema_version"])
            self.assertEqual([], migrated.list_optimization_profiles())
            profile = migrated.create_optimization_profile(
                "Migrated preferences",
                allocation_strategy="role_priority",
                characters=[],
            )
            self.assertEqual(1, profile["version"]["version_number"])

    def test_failed_v5_migration_rolls_back_ddl_and_can_retry(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "failed_v5_migration.sqlite3"
        with UserDataDao(legacy_path, account_id="legacy"):
            pass

        connection = sqlite3.connect(legacy_path)
        for table in (
            "character_shape_bonus_preference_property",
            "character_shape_bonus_preference",
            "ui_item_order",
            "application_setting_migration",
            "application_setting_copy",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DROP TABLE character_weight_preference_property")
        connection.execute("DROP TABLE character_weight_preference_seed")
        for table in (
            "character_profile_skill",
            "character_profile",
            "optimization_preference_property_limit",
            "optimization_preference_substat_priority",
            "optimization_preference_property_weight",
            "optimization_preference_character",
            "optimization_preference_version",
            "optimization_preference_profile",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migration WHERE version >= 5")
        connection.commit()
        connection.close()

        original_migration = user_data_dao_module.USER_MIGRATIONS[5]
        user_data_dao_module.USER_MIGRATIONS[5] = SimpleNamespace(
            is_file=lambda: True,
            read_text=lambda **_kwargs: """
                CREATE TABLE optimization_preference_profile (profile_id INTEGER PRIMARY KEY);
                CREATE TABLE migration_failure_probe (id INTEGER PRIMARY KEY);
                this is deliberately invalid SQL;
            """,
        )
        try:
            with self.assertRaises(UserDataError):
                UserDataDao(legacy_path)
        finally:
            user_data_dao_module.USER_MIGRATIONS[5] = original_migration

        connection = sqlite3.connect(legacy_path)
        self.assertEqual(
            4,
            connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0],
        )
        self.assertEqual(
            [],
            connection.execute(
                """SELECT name FROM sqlite_master WHERE type = 'table'
                   AND name IN ('optimization_preference_profile', 'migration_failure_probe')"""
            ).fetchall(),
        )
        connection.close()

        with UserDataDao(legacy_path) as migrated:
            self.assertEqual(10, migrated.summary()["schema_version"])

    def test_character_profiles_store_only_official_pointers_and_user_levels(self) -> None:
        saved = self.dao.save_character_profile(
            character_id=1051,
            character_level=80,
            breakthrough_stage=6,
            awakening_level=3,
            fork_id="fork_example",
            fork_level=80,
            fork_refinement_level=1,
            selected_skill_id="Skill1",
            skill_levels={"Skill1": 10, "UltraSkill": 8},
            ordinal=0,
        )

        self.assertEqual(1051, saved["character_id"])
        self.assertEqual("fork_example", saved["fork_id"])
        self.assertEqual({"Skill1": 10, "UltraSkill": 8}, saved["skill_levels"])
        columns = {
            row[1]
            for row in self.dao._db().execute("PRAGMA table_info(character_profile)")
        }
        self.assertNotIn("character_name", columns)
        self.assertNotIn("stats_json", columns)

    def test_character_weights_seed_once_and_remain_account_editable(self) -> None:
        seeded = self.dao.seed_character_weight_preferences(
            1075,
            source_dataset_id="fixture",
            source_kind="default",
            properties=[
                {"property_id": "CritBase", "weight": 1.0, "main_weight": 1.0},
                {"property_id": "AtkUp", "weight": 0.7, "main_weight": 0.4},
            ],
        )
        self.assertEqual({"CritBase": 1.0, "AtkUp": 0.7}, seeded["property_weights"])

        saved = self.dao.save_character_weight_preferences(
            1075,
            properties=[
                {"property_id": "CritBase", "weight": 1.25, "main_weight": 1.0},
                {"property_id": "AtkUp", "weight": 0.0, "main_weight": 0.4},
            ],
        )
        self.assertEqual({"CritBase": 1.25}, saved["property_weights"])
        reseeded = self.dao.seed_character_weight_preferences(
            1075,
            source_dataset_id="new-fixture",
            source_kind="workshop_api",
            properties=[{"property_id": "CritBase", "weight": 9.0, "main_weight": 9.0}],
        )
        self.assertEqual({"CritBase": 1.25}, reseeded["property_weights"])
        self.assertEqual("fixture", reseeded["source_dataset_id"])
        self.assertEqual("account", reseeded["source_kind"])

    def test_unmodified_weight_cache_refreshes_but_account_edit_does_not(self) -> None:
        self.dao.seed_character_weight_preferences(
            1075,
            source_dataset_id="public-v1",
            source_kind="default",
            properties=[
                {"property_id": "CritBase", "weight": 1.0, "main_weight": 1.0},
            ],
        )
        refreshed = self.dao.refresh_unmodified_character_weight_preferences(
            1075,
            source_dataset_id="public-v2",
            source_kind="default",
            properties=[
                {"property_id": "CritBase", "weight": 1.4, "main_weight": 0.8},
            ],
        )
        assert refreshed is not None
        self.assertEqual({"CritBase": 1.4}, refreshed["property_weights"])
        self.assertEqual("public-v2", refreshed["source_dataset_id"])
        self.assertEqual("default", refreshed["source_kind"])
        self.assertEqual(refreshed["seeded_at_utc"], refreshed["updated_at_utc"])

        customized = self.dao.save_character_weight_preferences(
            1075,
            properties=[
                {"property_id": "CritBase", "weight": 2.0, "main_weight": 1.2},
            ],
        )
        self.assertEqual("account", customized["source_kind"])
        self.assertIsNone(
            self.dao.refresh_unmodified_character_weight_preferences(
                1075,
                source_dataset_id="public-v3",
                source_kind="default",
                properties=[
                    {"property_id": "CritBase", "weight": 9.0, "main_weight": 9.0},
                ],
            )
        )
        self.assertEqual(
            {"CritBase": 2.0},
            self.dao.get_character_weight_preferences(1075)["property_weights"],
        )

    def test_character_weights_reject_negative_or_duplicate_properties(self) -> None:
        with self.assertRaises(UserDataValidationError):
            self.dao.seed_character_weight_preferences(
                1003,
                source_dataset_id="fixture",
                source_kind="default",
                properties=[{"property_id": "CritBase", "weight": -0.1}],
            )
        with self.assertRaises(UserDataValidationError):
            self.dao.seed_character_weight_preferences(
                1003,
                source_dataset_id="fixture",
                source_kind="default",
                properties=[
                    {"property_id": "CritBase", "weight": 1.0},
                    {"property_id": "CritBase", "weight": 0.5},
                ],
            )

    def test_character_shape_bonus_is_account_editable(self) -> None:
        saved = self.dao.save_character_shape_bonus_preferences(
            1075,
            shape_label="Type-4",
            property_values={"CritBase": 6.0, "AtkUp": 12.5},
        )

        self.assertEqual("Type-4", saved["shape_label"])
        self.assertEqual(
            {"CritBase": 6.0, "AtkUp": 12.5}, saved["property_values"],
        )
        self.assertEqual(saved, self.dao.get_character_shape_bonus_preferences(1075))
        with self.assertRaises(UserDataValidationError):
            self.dao.save_character_shape_bonus_preferences(
                1075, shape_label="Type-2", property_values={"CritBase": -0.1},
            )

    def test_migrates_v5_database_to_character_profile_pointers(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_v5.sqlite3"
        with UserDataDao(legacy_path, account_id="legacy") as initialized:
            initialized.create_optimization_profile(
                "existing-v5",
                allocation_strategy="role_priority",
                characters=[],
            )
        connection = sqlite3.connect(legacy_path)
        connection.execute("DROP TABLE character_weight_preference_property")
        connection.execute("DROP TABLE character_weight_preference_seed")
        connection.execute("DROP TABLE character_shape_bonus_preference_property")
        connection.execute("DROP TABLE character_shape_bonus_preference")
        connection.execute("DROP TABLE ui_item_order")
        connection.execute("DROP TABLE application_setting_migration")
        connection.execute("DROP TABLE application_setting_copy")
        connection.execute("DROP TABLE character_profile_skill")
        connection.execute("DROP TABLE character_profile")
        connection.execute("DELETE FROM schema_migration WHERE version >= 6")
        connection.commit()
        connection.close()

        with UserDataDao(legacy_path) as migrated:
            self.assertEqual(10, migrated.summary()["schema_version"])
            self.assertEqual("existing-v5", migrated.list_optimization_profiles()[0]["name"])
            self.assertEqual([], migrated.list_character_profiles())

    def test_character_profile_rejects_selected_skill_without_level_pointer(self) -> None:
        with self.assertRaises(UserDataValidationError):
            self.dao.save_character_profile(
                character_id=1051,
                character_level=80,
                breakthrough_stage=6,
                awakening_level=6,
                fork_id=None,
                fork_level=None,
                fork_refinement_level=None,
                selected_skill_id="Skill1",
                skill_levels={},
            )

    def test_versioned_optimization_preferences_preserve_history_and_support_retirement(self) -> None:
        initial_characters = [
            {
                "character_id": 1003,
                "ordinal": 0,
                "priority_group": 0,
                "target_suit_id": "Suit1",
                "suit_requirement_mode": "four_piece",
                "core_main_property_id": "DamageUp",
                "property_weights": {"CritDamageBase": 1.5, "AtkAdd": 0.8},
                "substat_priorities": ["CritDamageBase", "AtkAdd"],
                "property_limits": {"CritBase": {"minimum": 0.5, "maximum": 0.8}},
            },
            {
                "character_id": 1004,
                "ordinal": 1,
                "priority_group": 2,
                "suit_requirement_mode": "none",
                "property_weights": {},
                "substat_priorities": [],
                "property_limits": {},
            },
        ]
        profile = self.dao.create_optimization_profile(
            "Main allocation",
            allocation_strategy="role_priority",
            characters=initial_characters,
        )
        self.assertTrue(profile["is_active"])
        self.assertEqual("role_priority", profile["version"]["allocation_strategy"])
        self.assertEqual(1, profile["version"]["version_number"])
        self.assertEqual(
            {"CritDamageBase": 1.5, "AtkAdd": 0.8},
            profile["version"]["characters"][0]["property_weights"],
        )
        self.assertEqual(
            ["CritDamageBase", "AtkAdd"],
            profile["version"]["characters"][0]["substat_priorities"],
        )

        second_version = self.dao.create_optimization_profile_version(
            profile["profile_id"],
            allocation_strategy="global_optimal",
            characters=[
                {
                    **initial_characters[0],
                    "property_weights": {"CritDamageBase": 2.0},
                    "substat_priorities": ["CritDamageBase"],
                    "property_limits": {"CritBase": {"minimum": 0.6}},
                }
            ],
        )
        self.assertEqual(2, second_version["version_number"])
        latest = self.dao.get_optimization_profile(profile["profile_id"])
        original = self.dao.get_optimization_profile(profile["profile_id"], version_number=1)
        self.assertEqual("global_optimal", latest["version"]["allocation_strategy"])
        self.assertEqual(
            {"CritDamageBase": 2.0}, latest["version"]["characters"][0]["property_weights"]
        )
        self.assertEqual("role_priority", original["version"]["allocation_strategy"])
        self.assertEqual(
            {"CritDamageBase": 1.5, "AtkAdd": 0.8},
            original["version"]["characters"][0]["property_weights"],
        )

        self.assertTrue(self.dao.deactivate_optimization_profile(profile["profile_id"]))
        self.assertFalse(self.dao.deactivate_optimization_profile(profile["profile_id"]))
        self.assertEqual([], self.dao.list_optimization_profiles())
        retired_original = self.dao.get_optimization_profile(
            profile["profile_id"], version_number=1
        )
        self.assertEqual(1, retired_original["version"]["version_number"])
        retired = self.dao.list_optimization_profiles(include_inactive=True)
        self.assertFalse(retired[0]["is_active"])
        self.assertEqual(2, retired[0]["version"]["version_number"])

    def test_optimization_preferences_validate_constraints_and_stay_account_local(self) -> None:
        with self.assertRaises(UserDataValidationError):
            self.dao.create_optimization_profile(
                "Invalid limits",
                allocation_strategy="role_priority",
                characters=[
                    {
                        "character_id": 1003,
                        "property_limits": {"CritBase": {"minimum": 1.0, "maximum": 0.5}},
                    }
                ],
            )
        with self.assertRaises(UserDataValidationError):
            self.dao.create_optimization_profile(
                "Invalid strategy", allocation_strategy="unsupported", characters=[]
            )
        with self.assertRaises(UserDataValidationError):
            self.dao.create_optimization_profile(
                "Invalid suit requirement",
                allocation_strategy="role_priority",
                characters=[
                    {"character_id": 1003, "suit_requirement_mode": "four_piece"}
                ],
            )

        profile = self.dao.create_optimization_profile(
            "Database suit constraint", allocation_strategy="role_priority", characters=[]
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.dao._db().execute(
                """INSERT INTO optimization_preference_character(
                       profile_version_id, character_id, ordinal, priority_group,
                       target_suit_id, suit_requirement_mode, core_main_property_id
                   ) VALUES (?, 1003, 0, 0, NULL, 'four_piece', NULL)""",
                (profile["version"]["profile_version_id"],),
            )
        self.dao._db().rollback()

        original_insert = self.dao._insert_optimization_profile_version

        def fail_initial_version(*_args):
            raise sqlite3.OperationalError("forced failure")

        self.dao._insert_optimization_profile_version = fail_initial_version
        try:
            with self.assertRaises(UserDataError):
                self.dao.create_optimization_profile(
                    "Atomic initial version", allocation_strategy="role_priority", characters=[]
                )
        finally:
            self.dao._insert_optimization_profile_version = original_insert
        self.assertIsNone(
            self.dao._one(
                "SELECT profile_id FROM optimization_preference_profile WHERE name = ?",
                ("Atomic initial version",),
            )
        )

        second_database = Path(self.temp_dir.name) / "other_account.sqlite3"
        with UserDataDao(second_database, account_id="other") as other:
            self.dao.create_optimization_profile(
                "Only default", allocation_strategy="drive_priority", characters=[]
            )
            self.assertEqual([], other.list_optimization_profiles())

    def test_imports_complete_snapshot_and_keeps_raw_ids_and_stats(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(71, [item(100, 10), item(200, 20, "core")]),
            protocol_version=1,
        )
        summary = self.dao.current_inventory_summary()
        self.assertEqual(summary["snapshot_id"], snapshot_id)
        self.assertEqual(summary["module_count"], 1)
        self.assertEqual(summary["core_count"], 1)

        modules = self.dao.list_current_inventory_items(kind="module")
        self.assertEqual(modules[0]["item_id"], "cell3_style1_1_Orange")
        self.assertEqual(modules[0]["geometry"], "ZhiJiao1")
        self.assertFalse(modules[0]["discarded"])
        self.assertIsNone(modules[0]["equipped_placement"])
        self.assertEqual(modules[0]["main_stats"][0]["property_id"], "AtkUp")
        self.assertEqual(
            self.dao.raw_snapshot(snapshot_id)["method"], "event.inventory.snapshot"
        )

    def test_persists_character_instance_mapping_from_snapshot_and_manual_fallback(self) -> None:
        equipped = item(101, 11)
        equipped["equipped"] = True
        equipped["equipped_character_id"] = 1003
        equipped["equipped_character_uid"] = {"slot": 700, "serial": 701}
        self.dao.import_inventory_snapshot(snapshot(1, [equipped]))
        mappings = self.dao.list_character_instance_mappings(1003)
        self.assertEqual(1, len(mappings))
        self.assertEqual("snapshot", mappings[0]["source"])
        self.assertEqual((700, 701), (mappings[0]["uid_slot"], mappings[0]["uid_serial"]))

        self.dao.upsert_character_instance_mapping(1003, {"slot": 702, "serial": 703})
        self.assertEqual(
            {(700, 701), (702, 703)},
            {(row["uid_slot"], row["uid_serial"]) for row in self.dao.list_character_instance_mappings(1003)},
        )

    def test_persists_equipment_apply_job_log_and_retry_state(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(snapshot(1, [item(11, 22)]))
        plan_id = self.dao.save_loadout_plan(
            name="测试方案", character_id=1003, source_snapshot_id=snapshot_id,
            assignments=[{"uid_serial": 11, "uid_slot": 22, "kind": "module", "target_row": 1, "target_column": 1, "rotation": 0}],
        )
        job_id = self.dao.create_equipment_apply_job(snapshot_id, [{
            "role_name": "测试角色", "character_id": 1003,
            "character_uid": {"slot": 700, "serial": 701}, "plan_id": plan_id,
        }])
        job = self.dao.get_equipment_apply_job(job_id)
        job_item_id = job["items"][0]["job_item_id"]
        self.dao.mark_equipment_apply_job_item(job_item_id, status="running")
        self.dao.mark_equipment_apply_job_item(job_item_id, status="failed", error="网络中断")
        self.assertEqual("failed", self.dao.get_equipment_apply_job(job_id)["status"])
        self.dao.reset_failed_equipment_apply_job_items(job_id)
        self.assertEqual("pending", self.dao.get_equipment_apply_job(job_id)["items"][0]["status"])
        self.dao.mark_equipment_apply_job_item(job_item_id, status="running")
        self.dao.mark_equipment_apply_job_item(job_item_id, status="succeeded", before_snapshot_id=snapshot_id, after_snapshot_id=snapshot_id)
        self.assertTrue(self.dao.complete_equipment_apply_job_if_done(job_id))
        completed = self.dao.get_equipment_apply_job(job_id)
        self.assertEqual("completed", completed["status"])
        self.assertGreaterEqual(len(completed["logs"]), 5)

    def test_new_snapshot_atomically_replaces_current_and_invalid_one_does_not(self) -> None:
        first_id = self.dao.import_inventory_snapshot(snapshot(1, [item(1, 1)]))
        second_id = self.dao.import_inventory_snapshot(snapshot(2, [item(2, 2)]))
        snapshots = self.dao.list_inventory_snapshots()
        self.assertEqual([row["snapshot_id"] for row in snapshots if row["is_current"]], [second_id])
        self.assertNotEqual(first_id, second_id)

        invalid = snapshot(3, [item(3, 3)])
        invalid["params"]["item_count"] = 99
        with self.assertRaises(UserDataValidationError):
            self.dao.import_inventory_snapshot(invalid)
        self.assertEqual(self.dao.current_inventory_summary()["snapshot_id"], second_id)

    def test_calculation_can_pin_an_immutable_snapshot_while_current_changes(self) -> None:
        first_id = self.dao.import_inventory_snapshot(snapshot(1, [item(1, 1)]))
        pinned = self.dao.list_inventory_items(first_id)
        second_id = self.dao.import_inventory_snapshot(
            snapshot(2, [item(1, 1), item(2, 2)])
        )

        self.assertEqual(first_id, pinned[0]["snapshot_id"])
        self.assertEqual(1, len(self.dao.list_inventory_items(first_id)))
        self.assertEqual(2, len(self.dao.list_inventory_items(second_id)))
        self.assertEqual(second_id, self.dao.current_inventory_snapshot_id())
        self.assertEqual(first_id, self.dao.inventory_snapshot_summary(first_id)["snapshot_id"])
        diff = self.dao.inventory_snapshot_diff(first_id, second_id)
        self.assertEqual(1, diff["added_count"])
        self.assertEqual(0, diff["removed_count"])

    def test_inventory_uid_filter_keeps_only_requested_item_and_stats(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(snapshot(1, [
            item(1, 1), item(2, 2), item(3, 3),
        ]))

        rows = self.dao.list_inventory_items(snapshot_id, uids=[(2, 2)])

        self.assertEqual(1, len(rows))
        self.assertEqual((2, 2), (rows[0]["uid_serial"], rows[0]["uid_slot"]))
        self.assertEqual(["AtkUp"], [stat["property_id"] for stat in rows[0]["main_stats"]])
        self.assertEqual(
            ["CritBase", "AtkAdd"],
            [stat["property_id"] for stat in rows[0]["sub_stats"]],
        )

    def test_exports_snapshot_from_one_read_transaction_when_background_prunes(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(snapshot(1, [item(1, 1)]))
        original_summary = self.dao.inventory_snapshot_summary
        background = sqlite3.connect(self.database)
        background.execute("PRAGMA foreign_keys = ON")

        def summary_then_prune(requested_snapshot_id: int) -> dict | None:
            summary = original_summary(requested_snapshot_id)
            background.execute(
                "DELETE FROM inventory_snapshot WHERE snapshot_id = ?", (snapshot_id,)
            )
            background.commit()
            return summary

        try:
            with patch.object(
                self.dao, "inventory_snapshot_summary", side_effect=summary_then_prune
            ):
                summary, exported = self.dao.export_inventory_snapshot(snapshot_id)
        finally:
            background.close()

        self.assertEqual(snapshot_id, summary["snapshot_id"])
        self.assertEqual(1, summary["stored_item_count"])
        self.assertEqual(1, len(exported))
        self.assertIsNone(self.dao.inventory_snapshot_summary(snapshot_id))

    def test_rejects_snapshot_export_when_stored_item_count_is_inconsistent(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(snapshot(1, [item(1, 1)]))
        connection = self.dao._db()
        connection.execute("DELETE FROM inventory_item WHERE snapshot_id = ?", (snapshot_id,))
        connection.commit()

        with self.assertRaises(UserDataError):
            self.dao.export_inventory_snapshot(snapshot_id)

    def test_saves_loadout_using_native_uid_and_character_id(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(snapshot(1, [item(11, 22)]))
        plan_id = self.dao.save_loadout_plan(
            name="测试方案",
            character_id=1003,
            source_snapshot_id=snapshot_id,
            score=84.0,
            is_active=True,
            assignments=[
                {
                    "uid_serial": 11,
                    "uid_slot": 22,
                    "kind": "module",
                    "target_row": 2,
                    "target_column": 3,
                    "rotation": 0,
                }
            ],
            payload={"optimizer": "future-v2"},
        )
        plans = self.dao.list_loadout_plans(1003)
        self.assertEqual(plans[0]["plan_id"], plan_id)
        self.assertEqual(plans[0]["character_id"], 1003)
        self.assertEqual(plans[0]["assignments"][0]["uid_serial"], 11)
        self.assertTrue(plans[0]["is_active"])
        self.assertEqual(self.dao.get_loadout_plan(plan_id)["plan_id"], plan_id)
        self.assertIsNone(self.dao.get_loadout_plan(plan_id + 1000))

    def test_batch_replace_leaves_empty_changed_placeholder_for_other_active_role(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(1, [item(11, 22), item(12, 23), item(13, 24)])
        )
        old_plan_id = self.dao.save_loadout_plan(
            name="旧角色方案",
            character_id=1003,
            source_snapshot_id=snapshot_id,
            status="ready",
            is_active=True,
            assignments=[
                {
                    "uid_serial": 11, "uid_slot": 22, "kind": "module",
                    "target_row": 1, "target_column": 1, "rotation": 0,
                },
                {
                    "uid_serial": 12, "uid_slot": 23, "kind": "module",
                    "target_row": 2, "target_column": 2, "rotation": 0,
                },
            ],
            payload={"source_role_name": "旧角色"},
        )

        saved_ids = self.dao.replace_active_loadout_plans([{
            "name": "新角色方案",
            "character_id": 1055,
            "source_snapshot_id": snapshot_id,
            "status": "ready",
            "assignments": [
                {
                    "uid_serial": 11, "uid_slot": 22, "kind": "module",
                    "target_row": 1, "target_column": 1, "rotation": 0,
                },
                {
                    "uid_serial": 13, "uid_slot": 24, "kind": "module",
                    "target_row": 2, "target_column": 2, "rotation": 0,
                },
            ],
            "payload": {"source_role_name": "新角色"},
        }])

        self.assertEqual(1, len(saved_ids))
        self.assertFalse(self.dao.get_loadout_plan(old_plan_id)["is_active"])
        active = [plan for plan in self.dao.list_loadout_plans() if plan["is_active"]]
        self.assertEqual({1003, 1055}, {plan["character_id"] for plan in active})
        residual = next(plan for plan in active if plan["character_id"] == 1003)
        self.assertEqual((23, 12), (
            residual["assignments"][1]["uid_slot"],
            residual["assignments"][1]["uid_serial"],
        ))
        placeholder = residual["assignments"][0]
        self.assertEqual(0, placeholder["uid_slot"])
        self.assertTrue(placeholder["raw_assignment"]["virtual"])
        placeholder_uid = f"nte-module-0-{placeholder['uid_serial']}"
        self.assertEqual([placeholder_uid], residual["payload"]["changed_uids"])
        self.assertTrue(residual["payload"]["last_diff"]["changed"])
        self.assertEqual(
            placeholder_uid,
            residual["payload"]["last_diff"]["added"][0]["uid"],
        )
        self.assertEqual(
            old_plan_id,
            residual["payload"]["active_plan_overlay"]["previous_plan_id"],
        )
        self.assertEqual("active_plan_overlay", residual["payload"]["source"])
        active_uids = [
            (row["uid_slot"], row["uid_serial"])
            for plan in active
            for row in plan["assignments"]
        ]
        real_uids = [uid for uid in active_uids if uid[0] > 0]
        self.assertEqual(len(real_uids), len(set(real_uids)))

    def test_batch_replace_rolls_back_every_role_when_one_uid_is_missing(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(1, [item(11, 22), item(12, 23)])
        )
        old_plan_id = self.dao.save_loadout_plan(
            name="原方案",
            character_id=1003,
            source_snapshot_id=snapshot_id,
            status="ready",
            is_active=True,
            assignments=[{
                "uid_serial": 11, "uid_slot": 22, "kind": "module",
                "target_row": 1, "target_column": 1, "rotation": 0,
            }],
        )
        plan_count = len(self.dao.list_loadout_plans())

        with self.assertRaisesRegex(UserDataValidationError, "不在方案固定"):
            self.dao.replace_active_loadout_plans([
                {
                    "name": "第一角色",
                    "character_id": 1003,
                    "source_snapshot_id": snapshot_id,
                    "assignments": [{
                        "uid_serial": 12, "uid_slot": 23, "kind": "module",
                        "target_row": 1, "target_column": 1, "rotation": 0,
                    }],
                },
                {
                    "name": "第二角色",
                    "character_id": 1055,
                    "source_snapshot_id": snapshot_id,
                    "assignments": [{
                        "uid_serial": 99, "uid_slot": 99, "kind": "module",
                        "target_row": 1, "target_column": 1, "rotation": 0,
                    }],
                },
            ])

        self.assertEqual(plan_count, len(self.dao.list_loadout_plans()))
        self.assertTrue(self.dao.get_loadout_plan(old_plan_id)["is_active"])

    def test_batch_replace_rejects_uid_shared_by_incoming_roles(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(snapshot(1, [item(11, 22)]))
        shared_assignment = {
            "uid_serial": 11, "uid_slot": 22, "kind": "module",
            "target_row": 1, "target_column": 1, "rotation": 0,
        }

        with self.assertRaisesRegex(UserDataValidationError, "多个角色"):
            self.dao.replace_active_loadout_plans([
                {
                    "name": "第一角色", "character_id": 1003,
                    "source_snapshot_id": snapshot_id,
                    "assignments": [shared_assignment],
                },
                {
                    "name": "第二角色", "character_id": 1055,
                    "source_snapshot_id": snapshot_id,
                    "assignments": [shared_assignment],
                },
            ])

        self.assertEqual([], self.dao.list_loadout_plans())

    def test_batch_replace_keeps_plan_bound_to_its_source_snapshot(self) -> None:
        calculation_snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(1, [item(11, 22)])
        )
        self.dao.import_inventory_snapshot(snapshot(2, [item(12, 23)]))

        saved_ids = self.dao.replace_active_loadout_plans([{
            "name": "历史计算方案",
            "character_id": 1003,
            "source_snapshot_id": calculation_snapshot_id,
            "assignments": [{
                "uid_serial": 11, "uid_slot": 22, "kind": "module",
                "target_row": 1, "target_column": 1, "rotation": 0,
            }],
        }])

        self.assertEqual(1, len(saved_ids))
        self.assertEqual(
            calculation_snapshot_id,
            self.dao.get_loadout_plan(saved_ids[0])["source_snapshot_id"],
        )

    def test_finds_active_plan_by_ui_role_name_without_json_state(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(snapshot(1, [item(11, 22)]))
        plan_id = self.dao.save_loadout_plan(
            name="早雾官方方案",
            character_id=1003,
            source_snapshot_id=snapshot_id,
            is_active=True,
            assignments=[{
                "uid_serial": 11, "uid_slot": 22, "kind": "module",
                "target_row": 2, "target_column": 3, "rotation": 0,
            }],
            payload={"source_role_name": "早雾", "schema": "allocation-official-snapshot-v1"},
        )
        plan = self.dao.get_active_loadout_plan_for_role("早雾")
        self.assertEqual(plan_id, plan["plan_id"])
        self.assertEqual({"早雾": plan_id}, {
            name: row["plan_id"]
            for name, row in self.dao.list_active_loadout_plans_by_role().items()
        })

    def test_deactivates_active_plan_without_deleting_plan_history(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(snapshot(1, [item(11, 22)]))
        plan_id = self.dao.save_loadout_plan(
            name="早雾官方方案",
            character_id=1003,
            source_snapshot_id=snapshot_id,
            is_active=True,
            assignments=[{
                "uid_serial": 11, "uid_slot": 22, "kind": "module",
                "target_row": 2, "target_column": 3, "rotation": 0,
            }],
            payload={"source_role_name": "早雾", "schema": "allocation-official-snapshot-v1"},
        )

        self.assertTrue(self.dao.deactivate_loadout_plan(plan_id))
        self.assertFalse(self.dao.deactivate_loadout_plan(plan_id))
        self.assertIsNone(self.dao.get_active_loadout_plan_for_role("早雾"))
        self.assertFalse(self.dao.get_loadout_plan(plan_id)["is_active"])

    def test_prunes_only_snapshots_not_current_recent_or_referenced_by_plan(self) -> None:
        first_id = self.dao.import_inventory_snapshot(snapshot(1, [item(1, 1)]))
        second_id = self.dao.import_inventory_snapshot(snapshot(2, [item(2, 2)]))
        third_id = self.dao.import_inventory_snapshot(snapshot(3, [item(3, 3)]))
        self.dao.save_loadout_plan(
            name="保留历史方案",
            character_id=1003,
            source_snapshot_id=first_id,
            assignments=[
                {
                    "uid_serial": 1,
                    "uid_slot": 1,
                    "kind": "module",
                    "target_row": 1,
                    "target_column": 1,
                    "rotation": 0,
                }
            ],
        )

        result = self.dao.prune_inventory_snapshots(retain_recent=1)

        self.assertEqual([second_id], result["deleted_snapshot_ids"])
        self.assertEqual(1, result["deleted_snapshot_count"])
        self.assertEqual([third_id], result["current_snapshot_ids"])
        self.assertEqual([first_id], result["referenced_snapshot_ids"])
        self.assertEqual([third_id], result["recent_snapshot_ids"])
        self.assertEqual(
            {first_id, third_id},
            {row["snapshot_id"] for row in self.dao.list_inventory_snapshots()},
        )
        self.assertEqual(
            first_id,
            self.dao.list_loadout_plans(1003)[0]["source_snapshot_id"],
        )

    def test_foreign_keys_are_enabled(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.dao._db().execute(
                """
                INSERT INTO inventory_item(
                    snapshot_id, uid_serial, uid_slot, kind, item_id, level,
                    max_level, locked, equipped, names_json, suit_names_json,
                    raw_item_json
                ) VALUES (999, 1, 1, 'module', 'x', 0, 0, 0, 0, '{}', '{}', '{}')
                """
            )

        self.assertEqual(
            self.dao.integrity_check(),
            {"ok": True, "quick_check": ["ok"], "foreign_key_errors": []},
        )


if __name__ == "__main__":
    unittest.main()
