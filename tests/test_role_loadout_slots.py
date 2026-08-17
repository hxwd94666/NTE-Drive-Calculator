# 验证角色多配装槽位的账号库公共行为。
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from src.services.loadout_slot_selection_service import LoadoutSlotSelectionService
from src.storage.sqlite.loadout_slot_dao import PRIMARY_LOADOUT_SLOT_KEY
from src.storage.sqlite.user_data_dao import SCHEMA_VERSION, UserDataDao, UserDataValidationError


def inventory_snapshot() -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "event.inventory.snapshot",
        "params": {
            "complete": True,
            "generation": 1,
            "sequence": 2,
            "observed_at_unix_ms": 1_784_308_856_895,
            "item_count": 1,
            "items": [
                {
                    "uid": {"serial": 11, "slot": 22},
                    "kind": "module",
                    "item_id": "cell3_style1_1_Orange",
                    "suit_id": "Suit1",
                    "geometry": "ZhiJiao1",
                    "grid": 3,
                    "quality": "orange",
                    "level": 20,
                    "max_level": 20,
                    "locked": False,
                    "discarded": False,
                    "equipped": False,
                    "equipped_character_uid": None,
                    "equipped_character_id": None,
                    "equipped_placement": None,
                    "names": {"zh_cn": "测试驱动"},
                    "suit_names": {"zh_cn": "测试空幕"},
                    "main_stats": [],
                    "sub_stats": [],
                }
            ],
        },
    }


def assignment() -> dict:
    return {
        "uid_serial": 11,
        "uid_slot": 22,
        "kind": "module",
        "target_row": 2,
        "target_column": 3,
        "rotation": 0,
    }


def snapshot_with_uid(*, serial: int, slot: int, level: int = 20) -> dict:
    snapshot = deepcopy(inventory_snapshot())
    item = snapshot["params"]["items"][0]
    item["uid"] = {"serial": serial, "slot": slot}
    item["level"] = level
    return snapshot


class RoleLoadoutSlotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "user_data.sqlite3"
        self.dao = UserDataDao(
            self.database,
            account_id="slot-test",
            account_name="slot-test",
        )
        self.snapshot_id = self.dao.import_inventory_snapshot(inventory_snapshot())

    def tearDown(self) -> None:
        self.dao.close()
        self.temp_dir.cleanup()

    def test_legacy_active_save_creates_primary_slot_and_updates_current_pointer(self) -> None:
        plan_id = self.dao.save_loadout_plan(
            name="主力方案",
            character_id=1003,
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            status="ready",
            is_active=True,
            payload={"schema": "allocation-official-snapshot-v1"},
        )

        slots = self.dao.list_loadout_slots(1003)

        self.assertEqual(1, len(slots))
        self.assertEqual(PRIMARY_LOADOUT_SLOT_KEY, slots[0]["slot_key"])
        self.assertEqual(plan_id, slots[0]["current_plan_id"])
        self.assertEqual(plan_id, slots[0]["current_plan"]["plan_id"])
        self.assertEqual(slots[0]["slot_id"], self.dao.get_loadout_plan(plan_id)["slot_id"])

    def test_secondary_slot_preserves_an_independent_current_plan(self) -> None:
        primary_plan_id = self.dao.save_loadout_plan(
            name="主力方案",
            character_id=1003,
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            status="ready",
            is_active=True,
            payload={"schema": "allocation-official-snapshot-v1"},
        )
        secondary_slot_id = self.dao.create_loadout_slot(1003, "副本", slot_key="raid")
        secondary_plan_id = self.dao.save_plan_to_slot(
            secondary_slot_id,
            name="副本方案",
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            payload={"schema": "allocation-official-snapshot-v1"},
        )

        slots = self.dao.list_loadout_slots(1003)
        by_key = {slot["slot_key"]: slot for slot in slots}

        self.assertEqual(primary_plan_id, by_key["primary"]["current_plan_id"])
        self.assertEqual(secondary_plan_id, by_key["raid"]["current_plan_id"])
        self.assertTrue(self.dao.get_loadout_plan(primary_plan_id)["is_active"])
        self.assertFalse(self.dao.get_loadout_plan(secondary_plan_id)["is_active"])
        self.assertEqual(secondary_slot_id, self.dao.get_loadout_plan(secondary_plan_id)["slot_id"])

    def test_replacement_transfer_releases_other_character_current_slot(self) -> None:
        owner_plan_id = self.dao.save_loadout_plan(
            name="原拥有者",
            character_id=1003,
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            status="saved",
            is_active=True,
            payload={"assignment_scores": {"nte-module-22-11": 25.0}},
        )
        target_slot_id = self.dao.create_loadout_slot(1004, "第二配装", slot_key="raid")
        self.dao.save_plan_to_slot(
            target_slot_id,
            name="目标旧方案",
            assignments=[],
            source_snapshot_id=self.snapshot_id,
            status="saved",
        )

        target_plan_id = self.dao.save_replacement_plan_to_slot(
            target_slot_id,
            name="目标新方案",
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            status="saved",
            score=25.0,
            payload={"assignment_scores": {"nte-module-22-11": 25.0}},
        )

        target = self.dao.get_loadout_slot(target_slot_id)["current_plan"]
        owner_slot = self.dao.list_loadout_slots(1003)[0]
        released_owner = owner_slot["current_plan"]
        self.assertEqual(target_plan_id, target["plan_id"])
        self.assertEqual((22, 11), (
            target["assignments"][0]["uid_slot"], target["assignments"][0]["uid_serial"]
        ))
        self.assertNotEqual(owner_plan_id, released_owner["plan_id"])
        self.assertEqual("incomplete", released_owner["status"])
        self.assertEqual(0, released_owner["assignments"][0]["uid_slot"])
        self.assertTrue(released_owner["assignments"][0]["raw_assignment"]["virtual"])

    def test_replacement_keeps_same_character_other_slot_equipment(self) -> None:
        self.dao.save_loadout_plan(
            name="主力方案",
            character_id=1003,
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            status="saved",
            is_active=True,
            payload={"assignment_scores": {"nte-module-22-11": 25.0}},
        )
        secondary_slot_id = self.dao.create_loadout_slot(1003, "副本", slot_key="raid")

        self.dao.save_replacement_plan_to_slot(
            secondary_slot_id,
            name="副本替换",
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            status="saved",
            score=25.0,
            payload={"assignment_scores": {"nte-module-22-11": 25.0}},
        )

        slots = self.dao.list_loadout_slots(1003)
        assignments = [
            item
            for slot in slots
            for item in slot["current_plan"]["assignments"]
        ]
        self.assertEqual({(22, 11)}, {
            (item["uid_slot"], item["uid_serial"])
            for item in assignments
        })
        self.assertTrue(all(item["uid_slot"] > 0 for item in assignments))

    def test_replacement_does_not_cross_release_two_visual_snapshots(self) -> None:
        visual_owner_snapshot = self.dao.import_inventory_snapshot(
            snapshot_with_uid(serial=7, slot=8), source="vision"
        )
        visual_target_snapshot = self.dao.import_inventory_snapshot(
            snapshot_with_uid(serial=7, slot=8, level=19), source="vision"
        )
        self.assertNotEqual(visual_owner_snapshot, visual_target_snapshot)
        owner_slot_id = self.dao.create_loadout_slot(1003, "视觉旧槽", slot_key="visual-old")
        target_slot_id = self.dao.create_loadout_slot(1004, "视觉新槽", slot_key="visual-new")
        visual_assignment = {**assignment(), "uid_serial": 7, "uid_slot": 8}
        self.dao.save_plan_to_slot(
            owner_slot_id, name="视觉旧方案", assignments=[visual_assignment],
            source_snapshot_id=visual_owner_snapshot, status="saved",
        )
        self.dao.save_plan_to_slot(
            target_slot_id, name="视觉新方案", assignments=[],
            source_snapshot_id=visual_target_snapshot, status="saved",
        )

        self.dao.save_replacement_plan_to_slot(
            target_slot_id, name="视觉替换", assignments=[visual_assignment],
            source_snapshot_id=visual_target_snapshot, status="saved",
        )

        owner = self.dao.get_loadout_slot(owner_slot_id)["current_plan"]
        self.assertEqual((8, 7), (owner["assignments"][0]["uid_slot"], owner["assignments"][0]["uid_serial"]))
        self.assertEqual("saved", owner["status"])

    def test_replacement_releases_same_native_uid_across_native_snapshots(self) -> None:
        native_owner_snapshot = self.dao.import_inventory_snapshot(
            snapshot_with_uid(serial=17, slot=18), source="nte_core"
        )
        native_target_snapshot = self.dao.import_inventory_snapshot(
            snapshot_with_uid(serial=17, slot=18, level=19), source="nte_core"
        )
        self.assertNotEqual(native_owner_snapshot, native_target_snapshot)
        owner_slot_id = self.dao.create_loadout_slot(1003, "抓包旧槽", slot_key="native-old")
        target_slot_id = self.dao.create_loadout_slot(1004, "抓包新槽", slot_key="native-new")
        native_assignment = {**assignment(), "uid_serial": 17, "uid_slot": 18}
        self.dao.save_plan_to_slot(
            owner_slot_id, name="抓包旧方案", assignments=[native_assignment],
            source_snapshot_id=native_owner_snapshot, status="saved",
        )
        self.dao.save_plan_to_slot(
            target_slot_id, name="抓包新方案", assignments=[],
            source_snapshot_id=native_target_snapshot, status="saved",
        )

        self.dao.save_replacement_plan_to_slot(
            target_slot_id, name="抓包替换", assignments=[native_assignment],
            source_snapshot_id=native_target_snapshot, status="saved",
        )

        owner = self.dao.get_loadout_slot(owner_slot_id)["current_plan"]
        self.assertEqual(0, owner["assignments"][0]["uid_slot"])
        self.assertEqual("incomplete", owner["status"])

    def test_batch_slot_save_is_atomic_when_later_plan_is_invalid(self) -> None:
        self.dao.save_loadout_plan(
            name="主力方案",
            character_id=1003,
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            status="ready",
            is_active=True,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "测试"},
        )
        primary = self.dao.list_loadout_slots(1003)[0]
        secondary_slot_id = self.dao.create_loadout_slot(1003, "副本", slot_key="raid")
        with self.assertRaises(UserDataValidationError):
            self.dao.save_plans_to_slots((
                {
                    "slot_id": primary["slot_id"],
                    "name": "新版主力",
                    "assignments": [assignment()],
                    "source_snapshot_id": self.snapshot_id,
                    "payload": {"source_role_name": "测试"},
                },
                {
                    "slot_id": secondary_slot_id,
                    "name": "无效副本",
                    "assignments": [{"uid_slot": 22, "uid_serial": 11, "kind": "invalid"}],
                    "source_snapshot_id": self.snapshot_id,
                    "payload": {"source_role_name": "测试"},
                },
            ))
        self.assertEqual("主力方案", self.dao.get_loadout_slot(primary["slot_id"])["current_plan"]["name"])
        self.assertIsNone(self.dao.get_loadout_slot(secondary_slot_id)["current_plan"])

    def test_deactivate_detaches_secondary_slot_current_plan(self) -> None:
        secondary_slot_id = self.dao.create_loadout_slot(1003, "副本", slot_key="raid")
        plan_id = self.dao.save_plan_to_slot(
            secondary_slot_id,
            name="副本方案",
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "测试"},
        )

        self.assertTrue(self.dao.deactivate_loadout_plan(plan_id))
        self.assertIsNone(self.dao.get_loadout_slot(secondary_slot_id)["current_plan_id"])
        self.assertFalse(self.dao.get_loadout_plan(plan_id)["is_active"])

    def test_v14_active_plan_migrates_to_primary_slot_without_payload_loss(self) -> None:
        plan_id = self.dao.save_loadout_plan(
            name="主力方案",
            character_id=1003,
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            status="ready",
            is_active=True,
            payload={
                "schema": "allocation-official-snapshot-v1",
                "source_role_name": "测试",
                "assignment_scores": {"nte-module-22-11": 20.0},
                "tape_main_values": {"nte-core-99-98": 123.0},
            },
        )
        self.dao.close()
        connection = sqlite3.connect(self.database)
        connection.execute("DROP INDEX idx_loadout_plan_active_slot")
        connection.execute("DROP INDEX idx_loadout_plan_slot")
        connection.execute("DROP INDEX idx_role_loadout_slot_character")
        connection.execute("ALTER TABLE loadout_plan DROP COLUMN slot_id")
        connection.execute("DROP TABLE role_loadout_slot")
        connection.execute("DROP TABLE inventory_item_runtime_state")
        connection.execute(
            "ALTER TABLE optimization_preference_substat_behavior "
            "DROP COLUMN blacklist_zero_weight"
        )
        connection.execute(
            "CREATE UNIQUE INDEX idx_loadout_plan_active_character "
            "ON loadout_plan(character_id) WHERE is_active = 1"
        )
        connection.execute("DELETE FROM schema_migration WHERE version >= 15")
        connection.commit()
        connection.close()

        self.dao = UserDataDao(self.database)
        slots = self.dao.list_loadout_slots(1003)
        migrated_plan = self.dao.get_loadout_plan(plan_id)

        self.assertEqual(SCHEMA_VERSION, self.dao.summary()["schema_version"])
        self.assertEqual(plan_id, slots[0]["current_plan_id"])
        self.assertEqual(PRIMARY_LOADOUT_SLOT_KEY, slots[0]["slot_key"])
        self.assertEqual("测试", slots[0]["slot_name"])
        self.assertEqual(slots[0]["slot_id"], migrated_plan["slot_id"])
        self.assertEqual(20.0, migrated_plan["payload"]["assignment_scores"]["nte-module-22-11"])
        self.assertEqual(123.0, migrated_plan["payload"]["tape_main_values"]["nte-core-99-98"])

    def test_secondary_current_plan_can_be_locked_and_reserves_its_equipment(self) -> None:
        self.dao.save_loadout_plan(
            name="主力方案",
            character_id=1003,
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            status="ready",
            is_active=True,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "测试"},
        )
        secondary_slot_id = self.dao.create_loadout_slot(1003, "副本", slot_key="raid")
        secondary_plan_id = self.dao.save_plan_to_slot(
            secondary_slot_id,
            name="副本方案",
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "测试"},
        )

        self.assertTrue(self.dao.set_allocation_lock(secondary_plan_id, True))
        locked = self.dao.list_allocation_locked_loadout_plans()

        self.assertEqual([secondary_plan_id], [plan["plan_id"] for plan in locked])
        with self.assertRaises(UserDataValidationError):
            self.dao.save_plan_to_slot(
                secondary_slot_id,
                name="覆盖副本",
                assignments=[assignment()],
                source_snapshot_id=self.snapshot_id,
                payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "测试"},
            )

    def test_slot_limit_and_archive_promotes_remaining_slot(self) -> None:
        self.dao.save_loadout_plan(
            name="主力方案",
            character_id=1003,
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            status="ready",
            is_active=True,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "测试"},
        )
        primary = self.dao.list_loadout_slots(1003)[0]
        raid = self.dao.create_loadout_slot(1003, "副本", slot_key="raid")
        self.dao.create_loadout_slot(1003, "测试", slot_key="test")

        with self.assertRaises(UserDataValidationError):
            self.dao.create_loadout_slot(1003, "额外", slot_key="extra")
        self.assertTrue(self.dao.archive_loadout_slot(primary["slot_id"]))
        self.assertEqual(
            {"primary", "test"},
            {slot["slot_key"] for slot in self.dao.list_loadout_slots(1003)},
        )
        promoted = next(
            slot
            for slot in self.dao.list_loadout_slots(1003)
            if slot["slot_key"] == "primary"
        )
        self.assertEqual(raid, promoted["slot_id"])
        self.assertFalse(self.dao.get_loadout_plan(primary["current_plan_id"])["is_active"])

    def test_role_default_selection_uses_current_primary_then_current_fallback(self) -> None:
        primary_plan_id = self.dao.save_loadout_plan(
            name="主槽方案",
            character_id=1003,
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            is_active=True,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "测试"},
        )
        secondary_slot_id = self.dao.create_loadout_slot(1003, "副本", slot_key="raid")
        secondary_plan_id = self.dao.save_plan_to_slot(
            secondary_slot_id,
            name="副本方案",
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "测试"},
        )

        selected = LoadoutSlotSelectionService(self.dao).resolve_default_roles(["测试"])
        self.assertEqual([primary_plan_id], [row.plan_id for row in selected])
        self.assertEqual("测试", self.dao.list_loadout_slots(1003)[0]["slot_name"])

        self.assertTrue(self.dao.deactivate_loadout_plan(primary_plan_id))
        selected = LoadoutSlotSelectionService(self.dao).resolve_default_roles(["测试"])
        self.assertEqual([secondary_plan_id], [row.plan_id for row in selected])

    def test_only_slot_cannot_be_archived_and_primary_can_be_renamed(self) -> None:
        plan_id = self.dao.save_loadout_plan(
            name="默认方案",
            character_id=1003,
            assignments=[assignment()],
            source_snapshot_id=self.snapshot_id,
            status="ready",
            is_active=True,
            payload={"schema": "allocation-official-snapshot-v1"},
        )
        primary = self.dao.list_loadout_slots(1003)[0]

        self.assertTrue(self.dao.rename_loadout_slot(primary["slot_id"], "输出方案"))
        self.assertEqual("输出方案", self.dao.get_loadout_slot(primary["slot_id"])["slot_name"])
        with self.assertRaises(UserDataValidationError):
            self.dao.archive_loadout_slot(primary["slot_id"])
        self.assertTrue(self.dao.get_loadout_plan(plan_id)["is_active"])


if __name__ == "__main__":
    unittest.main()
