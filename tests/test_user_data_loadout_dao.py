# 测试分账号用户数据库的初始化、快照和装配方案读写。
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import (
    UserDataDao,
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


class UserDataLoadoutDaoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "user_data.sqlite3"
        self.dao = UserDataDao(
            self.database, account_id="default", account_name="默认账号"
        )

    def tearDown(self) -> None:
        self.dao.close()
        self.temp_dir.cleanup()

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
            score=60.0,
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
            payload={
                "source_role_name": "旧角色",
                "assignment_scores": {
                    "nte-module-22-11": 25.0,
                    "nte-module-23-12": 35.0,
                },
            },
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
        self.assertEqual(35.0, residual["score"])
        self.assertEqual(
            0.0,
            residual["payload"]["assignment_scores"][placeholder_uid],
        )
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
