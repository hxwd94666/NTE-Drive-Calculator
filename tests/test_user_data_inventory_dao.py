# 测试分账号用户数据库的初始化、快照和装配方案读写。
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class UserDataInventoryDaoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "user_data.sqlite3"
        self.dao = UserDataDao(
            self.database, account_id="default", account_name="默认账号"
        )

    def tearDown(self) -> None:
        self.dao.close()
        self.temp_dir.cleanup()

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

    def test_runtime_state_delta_projects_current_snapshot_without_replacing_it(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(1, [item(1, 1), item(2, 2)])
        )
        changed = item(1, 1)
        changed.update({
            "locked": True,
            "discarded": True,
            "equipped": True,
            "equipped_character_id": 1003,
            "equipped_character_uid": {"slot": 700, "serial": 701},
            "equipped_placement": {"row": 2, "column": 3},
        })

        updated = self.dao.apply_inventory_runtime_state_delta(
            snapshot_id,
            [changed],
            observed_at_unix_ms=1_784_308_856_999,
            sequence=9,
        )

        self.assertEqual(1, updated)
        self.assertEqual(snapshot_id, self.dao.current_inventory_snapshot_id())
        immutable = self.dao.list_inventory_items(snapshot_id)
        self.assertFalse(immutable[0]["equipped"])
        projected = self.dao.list_inventory_items_with_runtime_state(snapshot_id)
        self.assertTrue(projected[0]["equipped"])
        self.assertTrue(projected[0]["locked"])
        self.assertTrue(projected[0]["discarded"])
        self.assertEqual(1003, projected[0]["equipped_character_id"])
        self.assertEqual({"row": 2, "column": 3}, projected[0]["equipped_placement"])

        stale = dict(changed)
        stale["equipped"] = False
        self.dao.apply_inventory_runtime_state_delta(snapshot_id, [stale], sequence=8)
        self.assertTrue(
            self.dao.list_inventory_items_with_runtime_state(snapshot_id)[0]["equipped"]
        )

    def test_command_state_projection_updates_only_known_rows_and_later_event_wins(self) -> None:
        """A submitted command updates the warehouse projection without moving the snapshot."""

        snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(1, [item(1, 1), item(2, 2)])
        )
        requested = item(1, 1)
        requested.update({
            "equipped": True,
            "equipped_character_id": 1003,
            "equipped_character_uid": {"slot": 700, "serial": 701},
            "equipped_placement": {"row": 2, "column": 3},
        })

        updated = self.dao.apply_inventory_command_state_projection(
            snapshot_id, [requested]
        )

        self.assertEqual(1, updated)
        self.assertEqual(snapshot_id, self.dao.current_inventory_snapshot_id())
        self.assertTrue(
            self.dao.list_inventory_items_with_runtime_state(snapshot_id)[0]["equipped"]
        )

        observed = dict(requested)
        observed.update({
            "equipped": False,
            "equipped_character_id": None,
            "equipped_character_uid": None,
            "equipped_placement": None,
        })
        self.dao.apply_inventory_runtime_state_delta(snapshot_id, [observed], sequence=1)

        self.assertFalse(
            self.dao.list_inventory_items_with_runtime_state(snapshot_id)[0]["equipped"]
        )

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

    def test_reads_two_thousand_inventory_items_without_exceeding_sqlite_variable_limit(self) -> None:
        """Warehouse reads every UID, so this must work on SQLite's 999-bind build."""
        rows = [item(index, index) for index in range(1, 2001)]
        snapshot_id = self.dao.import_inventory_snapshot(snapshot(1, rows))

        loaded = self.dao.list_inventory_items(snapshot_id)

        self.assertEqual(2000, len(loaded))
        self.assertEqual(2000, sum(len(row["main_stats"]) for row in loaded))
        self.assertEqual(4000, sum(len(row["sub_stats"]) for row in loaded))

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

if __name__ == "__main__":
    unittest.main()
