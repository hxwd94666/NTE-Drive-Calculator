# 测试分账号用户数据库的初始化、快照和装配方案读写。
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError


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
        self.assertEqual(summary["schema_version"], 4)
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
            self.assertEqual(4, migrated.summary()["schema_version"])
            self.assertEqual("旧账号", migrated.profile()["account_name"])
            self.assertEqual(5.0, settings["inventory_settle_seconds"])
            self.assertFalse(settings["auto_start_inventory_sync"])
            self.assertEqual(20, settings["inventory_snapshot_retention_count"])

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
