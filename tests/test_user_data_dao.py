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
        "equipped": False,
        "equipped_character_uid": None,
        "equipped_character_id": None,
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
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["profile"]["account_id"], "default")
        self.assertEqual(summary["sync_settings"]["inventory_sync_method"], "nte_core")

        settings = self.dao.update_sync_settings(
            inventory_sync_method="gamepad",
            equipment_apply_method="nte_core",
            raw_capture_enabled=True,
            inventory_settle_seconds=8.5,
        )
        self.assertEqual(settings["inventory_sync_method"], "gamepad")
        self.assertTrue(settings["raw_capture_enabled"])
        with self.assertRaises(UserDataValidationError):
            self.dao.update_sync_settings(inventory_sync_method="legacy_json")

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
        self.assertEqual(modules[0]["main_stats"][0]["property_id"], "AtkUp")
        self.assertEqual(
            self.dao.raw_snapshot(snapshot_id)["method"], "event.inventory.snapshot"
        )

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
