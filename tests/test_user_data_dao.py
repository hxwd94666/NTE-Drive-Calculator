# 测试分账号用户数据库的初始化、快照和装配方案读写。
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import src.storage.sqlite.user_data_dao as user_data_dao_module
from src.storage.sqlite.user_data_dao import (
    SCHEMA_VERSION,
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


def drop_battle_report_v13(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE battle_report_page_state")
    connection.execute("DROP TABLE battle_record_retention")
    connection.execute("DROP TABLE battle_record")


def drop_role_loadout_slots_v15(connection: sqlite3.Connection) -> None:
    """Downgrade a freshly initialized fixture to its v14 loadout shape."""

    connection.execute("DROP INDEX IF EXISTS idx_loadout_plan_active_slot")
    connection.execute("DROP INDEX IF EXISTS idx_loadout_plan_slot")
    connection.execute("DROP INDEX IF EXISTS idx_role_loadout_slot_character")
    connection.execute("ALTER TABLE loadout_plan DROP COLUMN slot_id")
    connection.execute("DROP TABLE role_loadout_slot")
    connection.execute(
        "CREATE UNIQUE INDEX idx_loadout_plan_active_character "
        "ON loadout_plan(character_id) WHERE is_active = 1"
    )


def drop_inventory_runtime_state_v21(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE IF EXISTS inventory_item_runtime_state")


def drop_blacklist_zero_weight_v22(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE optimization_preference_substat_behavior "
        "DROP COLUMN blacklist_zero_weight"
    )


class UserDataDaoSettingsTests(unittest.TestCase):
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
        self.assertEqual(summary["schema_version"], SCHEMA_VERSION)
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
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
            self.assertEqual("旧账号", migrated.profile()["account_name"])
            self.assertEqual(5.0, settings["inventory_settle_seconds"])
            self.assertFalse(settings["auto_start_inventory_sync"])
            self.assertEqual(20, settings["inventory_snapshot_retention_count"])

    def test_migrates_v4_database_to_versioned_optimization_preferences(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_v4.sqlite3"
        with UserDataDao(legacy_path, account_id="legacy") as initialized:
            self.assertEqual(SCHEMA_VERSION, initialized.summary()["schema_version"])

        connection = sqlite3.connect(legacy_path)
        drop_role_loadout_slots_v15(connection)
        drop_battle_report_v13(connection)
        drop_inventory_runtime_state_v21(connection)
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
            "optimization_preference_substat_behavior",
            "optimization_preference_substat_blacklist",
            "optimization_preference_property_limit",
            "optimization_preference_substat_priority",
            "optimization_preference_property_weight",
            "optimization_preference_character",
            "optimization_preference_version",
            "optimization_preference_profile",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migration WHERE version >= 5")
        connection.execute("DROP INDEX IF EXISTS idx_loadout_plan_active_allocation_locked")
        connection.execute("ALTER TABLE loadout_plan DROP COLUMN allocation_locked")
        connection.commit()
        connection.close()

        with UserDataDao(legacy_path) as migrated:
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
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
        drop_role_loadout_slots_v15(connection)
        drop_battle_report_v13(connection)
        drop_inventory_runtime_state_v21(connection)
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
            "optimization_preference_substat_behavior",
            "optimization_preference_substat_blacklist",
            "optimization_preference_property_limit",
            "optimization_preference_substat_priority",
            "optimization_preference_property_weight",
            "optimization_preference_character",
            "optimization_preference_version",
            "optimization_preference_profile",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migration WHERE version >= 5")
        connection.execute("DROP INDEX IF EXISTS idx_loadout_plan_active_allocation_locked")
        connection.execute("ALTER TABLE loadout_plan DROP COLUMN allocation_locked")
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
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])

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

    def test_reset_character_profiles_only_clears_role_pointers(self) -> None:
        for character_id in (1051, 1055):
            self.dao.save_character_profile(
                character_id=character_id,
                character_level=80,
                breakthrough_stage=6,
                awakening_level=3,
                fork_id="fork_example",
                fork_level=80,
                fork_refinement_level=1,
                selected_skill_id="Skill1",
                skill_levels={"Skill1": 10},
            )

        self.assertTrue(self.dao.reset_character_profile(1051))
        self.assertIsNone(self.dao.get_character_profile(1051))
        self.assertIsNotNone(self.dao.get_character_profile(1055))
        self.assertEqual(1, self.dao.reset_all_character_profiles())
        self.assertEqual([], self.dao.list_character_profiles(include_inactive=True))

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
        drop_role_loadout_slots_v15(connection)
        drop_battle_report_v13(connection)
        drop_inventory_runtime_state_v21(connection)
        connection.execute("DROP TABLE character_weight_preference_property")
        connection.execute("DROP TABLE character_weight_preference_seed")
        connection.execute("DROP TABLE character_shape_bonus_preference_property")
        connection.execute("DROP TABLE character_shape_bonus_preference")
        connection.execute("DROP TABLE ui_item_order")
        connection.execute("DROP TABLE application_setting_migration")
        connection.execute("DROP TABLE application_setting_copy")
        connection.execute("DROP TABLE character_profile_skill")
        connection.execute("DROP TABLE character_profile")
        connection.execute("DROP TABLE optimization_preference_substat_behavior")
        connection.execute("DROP TABLE optimization_preference_substat_blacklist")
        connection.execute("DELETE FROM schema_migration WHERE version >= 6")
        connection.execute("DROP INDEX IF EXISTS idx_loadout_plan_active_allocation_locked")
        connection.execute("ALTER TABLE loadout_plan DROP COLUMN allocation_locked")
        connection.commit()
        connection.close()

        with UserDataDao(legacy_path) as migrated:
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
            self.assertEqual("existing-v5", migrated.list_optimization_profiles()[0]["name"])
            self.assertEqual([], migrated.list_character_profiles())

    def test_migrates_v10_database_to_substat_blacklist_preferences(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_v10.sqlite3"
        with UserDataDao(legacy_path, account_id="legacy") as initialized:
            initialized.create_optimization_profile(
                "existing-v10",
                allocation_strategy="role_priority",
                characters=[
                    {
                        "character_id": 1003,
                        "ordinal": 0,
                        "priority_group": 0,
                        "suit_requirement_mode": "none",
                        "property_weights": {},
                        "substat_priorities": ["CritDamageBase"],
                        "property_limits": {},
                    }
                ],
            )
        connection = sqlite3.connect(legacy_path)
        drop_role_loadout_slots_v15(connection)
        drop_battle_report_v13(connection)
        drop_inventory_runtime_state_v21(connection)
        connection.execute("DROP TABLE optimization_preference_substat_behavior")
        connection.execute("DROP TABLE optimization_preference_substat_blacklist")
        connection.execute("DELETE FROM schema_migration WHERE version >= 11")
        connection.execute("DROP INDEX IF EXISTS idx_loadout_plan_active_allocation_locked")
        connection.execute("ALTER TABLE loadout_plan DROP COLUMN allocation_locked")
        connection.commit()
        connection.close()

        with UserDataDao(legacy_path) as migrated:
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
            self.assertEqual(
                "existing-v10", migrated.list_optimization_profiles()[0]["name"]
            )
            migrated_profile = migrated.list_optimization_profiles()[0]
            self.assertTrue(
                migrated_profile["version"]["characters"][0][
                    "ignore_grade_limit"
                ]
            )
            table = migrated._one(
                """SELECT name FROM sqlite_master
                   WHERE type = 'table'
                   AND name = 'optimization_preference_substat_blacklist'"""
            )
            self.assertIsNotNone(table)
            behavior_table = migrated._one(
                """SELECT name FROM sqlite_master
                   WHERE type = 'table'
                   AND name = 'optimization_preference_substat_behavior'"""
            )
            self.assertIsNotNone(behavior_table)

    def test_migrates_v21_database_to_blacklist_zero_weight(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_v21.sqlite3"
        with UserDataDao(legacy_path, account_id="legacy") as initialized:
            initialized.create_optimization_profile(
                "existing-v21",
                allocation_strategy="role_priority",
                characters=[
                    {
                        "character_id": 1003,
                        "ordinal": 0,
                        "priority_group": 0,
                        "suit_requirement_mode": "none",
                        "property_weights": {},
                        "substat_priorities": [],
                        "substat_blacklist": ["AtkAdd"],
                        "property_limits": {},
                    }
                ],
            )
        connection = sqlite3.connect(legacy_path)
        connection.execute(
            "ALTER TABLE optimization_preference_substat_behavior "
            "DROP COLUMN blacklist_zero_weight"
        )
        connection.execute("DELETE FROM schema_migration WHERE version >= 22")
        connection.commit()
        connection.close()

        with UserDataDao(legacy_path) as migrated:
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
            character = migrated.list_optimization_profiles()[0]["version"]["characters"][0]
            self.assertFalse(character["blacklist_zero_weight"])

    def test_migrates_v21_database_missing_substat_behavior_table(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_v21_missing_behavior.sqlite3"
        with UserDataDao(legacy_path, account_id="legacy"):
            pass
        connection = sqlite3.connect(legacy_path)
        connection.execute("DROP TABLE optimization_preference_substat_behavior")
        connection.execute("DELETE FROM schema_migration WHERE version >= 22")
        connection.commit()
        connection.close()

        with UserDataDao(legacy_path) as migrated:
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
            columns = {
                row["name"]
                for row in migrated._db().execute(
                    "PRAGMA table_info(optimization_preference_substat_behavior)"
                )
            }
            self.assertIn("blacklist_zero_weight", columns)

    def test_migrates_v11_database_to_allocation_plan_lock(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_v11.sqlite3"
        with UserDataDao(legacy_path, account_id="legacy") as initialized:
            self.assertEqual(SCHEMA_VERSION, initialized.summary()["schema_version"])
        connection = sqlite3.connect(legacy_path)
        drop_role_loadout_slots_v15(connection)
        drop_battle_report_v13(connection)
        drop_inventory_runtime_state_v21(connection)
        drop_blacklist_zero_weight_v22(connection)
        connection.execute("DROP INDEX idx_loadout_plan_active_allocation_locked")
        connection.execute("ALTER TABLE loadout_plan DROP COLUMN allocation_locked")
        connection.execute("DELETE FROM schema_migration WHERE version >= 12")
        connection.commit()
        connection.close()

        with UserDataDao(legacy_path) as migrated:
            self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
            columns = {
                row["name"]
                for row in migrated._db().execute("PRAGMA table_info(loadout_plan)")
            }
            self.assertIn("allocation_locked", columns)

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
                "substat_blacklist": ["DefAdd", "HPMaxAdd"],
                "blacklist_zero_weight": True,
                "equal_priority": True,
                "ignore_grade_limit": True,
                "min_grade_limit": "D",
                "crit_threshold": 65,
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
        self.assertEqual(
            ["DefAdd", "HPMaxAdd"],
            profile["version"]["characters"][0]["substat_blacklist"],
        )
        self.assertTrue(profile["version"]["characters"][0]["equal_priority"])
        self.assertTrue(profile["version"]["characters"][0]["blacklist_zero_weight"])
        self.assertTrue(
            profile["version"]["characters"][0]["ignore_grade_limit"]
        )
        self.assertEqual(
            "D", profile["version"]["characters"][0]["min_grade_limit"]
        )
        self.assertEqual(
            65.0, profile["version"]["characters"][0]["crit_threshold"]
        )

        second_version = self.dao.create_optimization_profile_version(
            profile["profile_id"],
            allocation_strategy="global_optimal",
            characters=[
                {
                    **initial_characters[0],
                    "property_weights": {"CritDamageBase": 2.0},
                    "substat_priorities": ["CritDamageBase"],
                    "substat_blacklist": ["HPMaxAdd"],
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
        self.assertEqual(
            ["HPMaxAdd"], latest["version"]["characters"][0]["substat_blacklist"]
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
                "Only default", allocation_strategy="global_optimal", characters=[]
            )
            self.assertEqual([], other.list_optimization_profiles())

    def test_legacy_drive_priority_profile_is_migrated_to_global_optimal(self) -> None:
        profile = self.dao.create_optimization_profile(
            "Legacy drive priority", allocation_strategy="drive_priority", characters=[]
        )

        self.assertEqual(
            "global_optimal", profile["version"]["allocation_strategy"],
        )

if __name__ == "__main__":
    unittest.main()
