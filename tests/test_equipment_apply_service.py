# 测试一键装配的能力检查、参数派发和新稳定快照验证。
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.equipment_apply_service import EquipmentApplyError, EquipmentApplyService
from src.services.inventory_sync_service import InventorySyncState
from src.storage.sqlite.user_data_dao import UserDataDao


CHARACTER_UID = {"slot": 7, "serial": 700}


def item(serial: int, kind: str, *, equipped: bool = False) -> dict:
    return {
        "uid": {"slot": serial, "serial": serial},
        "kind": kind,
        "item_id": "cell3_style1_1_Orange" if kind == "module" else "Attack_orange",
        "suit_id": "Suit1",
        "geometry": "Hen3" if kind == "module" else "Core",
        "grid": 3 if kind == "module" else None,
        "quality": "orange",
        "level": 20,
        "max_level": 20,
        "locked": False,
        "discarded": False,
        "equipped": equipped,
        "equipped_character_uid": dict(CHARACTER_UID) if equipped else None,
        "equipped_character_id": 1003 if equipped else None,
        "equipped_placement": {"row": 1, "column": 1} if equipped and kind == "module" else None,
        "names": {},
        "suit_names": {},
        "main_stats": [],
        "sub_stats": [],
    }


def snapshot(generation: int, items: list[dict], *, characters: list[dict] | None = None) -> dict:
    return {
        "method": "event.inventory.snapshot",
        "params": {
            "complete": True,
            "generation": generation,
            "sequence": generation,
            "observed_at_unix_ms": 1_800_000_000_000 + generation,
            "item_count": len(items),
            "items": items,
            "character_count": len(characters or []),
            "characters": characters or [],
        },
    }


class FakeSyncService:
    def __init__(self, dao: UserDataDao, before_snapshot_id: int) -> None:
        self.dao = dao
        self.is_running = True
        self.core_hello_result = {"capabilities": ["inventory", "equipment"]}
        self.state = InventorySyncState(
            phase="listening",
            running=True,
            last_snapshot_id=before_snapshot_id,
            last_item_count=2,
        )
        self.params = None
        self.module_calls = []
        self.module_dispatches = []
        self.unmount_calls = []
        self.verify_correctly = True
        self.emit_snapshot = True
        self.wait_calls = 0

    def equip_one_key(self, **kwargs):
        self.params = kwargs
        rows = [copy.deepcopy(item(11, "module")), copy.deepcopy(item(22, "core"))]
        for row in rows:
            row["equipped"] = True
            row["equipped_character_uid"] = dict(CHARACTER_UID)
            row["equipped_character_id"] = 1003
            if row["kind"] == "module":
                row["equipped_placement"] = {"row": 2, "column": 3}
        if not self.verify_correctly:
            rows[0]["equipped_placement"] = {"row": 5, "column": 5}
        if self.emit_snapshot:
            snapshot_id = self.dao.import_inventory_snapshot(snapshot(2, rows))
            self.state = InventorySyncState(
                phase="listening",
                running=True,
                last_snapshot_id=snapshot_id,
                last_item_count=2,
            )
        return {"status": "dispatched"}

    def equip_module(self, **kwargs):
        self.module_dispatches.append("equip")
        return self._apply_module(**kwargs)

    def move_module_to_character(self, **kwargs):
        self.module_dispatches.append("move")
        return self._apply_module(**kwargs)

    def unequip_all(self, **kwargs):
        self.unmount_calls.append(("all", kwargs))
        self._emit_snapshot([item(11, "module"), item(22, "core")])
        return {"status": "dispatched"}

    def unequip_module(self, **kwargs):
        self.unmount_calls.append(("module", kwargs))
        self._emit_snapshot([item(11, "module"), item(22, "core")])
        return {"status": "dispatched"}

    def unequip_core(self, **kwargs):
        self.unmount_calls.append(("core", kwargs))
        self._emit_snapshot([item(11, "module"), item(22, "core")])
        return {"status": "dispatched"}

    def _emit_snapshot(self, rows):
        snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(self.state.last_snapshot_id + 1, rows)
        )
        self.state = InventorySyncState(
            phase="listening",
            running=True,
            last_snapshot_id=snapshot_id,
            last_item_count=len(rows),
        )

    def _apply_module(self, **kwargs):
        self.module_calls.append(kwargs)
        rows = [copy.deepcopy(item(11, "module")), copy.deepcopy(item(22, "core"))]
        rows[0]["equipped"] = True
        rows[0]["equipped_character_uid"] = dict(CHARACTER_UID)
        rows[0]["equipped_character_id"] = 1003
        rows[0]["equipped_placement"] = {
            "row": kwargs["row"],
            "column": kwargs["column"],
        }
        if self.emit_snapshot:
            snapshot_id = self.dao.import_inventory_snapshot(snapshot(2, rows))
            self.state = InventorySyncState(
                phase="listening",
                running=True,
                last_snapshot_id=snapshot_id,
                last_item_count=2,
            )
        return {"status": "dispatched"}

    def wait_for_snapshot(self, *, after_snapshot_id=None, timeout=30.0):
        self.wait_calls += 1
        if self.state.last_snapshot_id <= after_snapshot_id:
            raise TimeoutError
        return self.state


class EquipmentApplyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dao = UserDataDao(
            Path(self.temp_dir.name) / "user.sqlite3", account_id="apply-test"
        )
        before = self.dao.import_inventory_snapshot(
            snapshot(1, [item(11, "module", equipped=True), item(22, "core")])
        )
        self.plan_id = self.dao.save_loadout_plan(
            name="装配测试",
            character_id=1003,
            source_snapshot_id=before,
            status="ready",
            assignments=[
                {
                    "uid_serial": 11,
                    "uid_slot": 11,
                    "kind": "module",
                    "target_row": 2,
                    "target_column": 3,
                    "rotation": 0,
                },
                {
                    "uid_serial": 22,
                    "uid_slot": 22,
                    "kind": "core",
                    "target_row": None,
                    "target_column": None,
                    "rotation": 0,
                },
            ],
        )
        self.sync = FakeSyncService(self.dao, before)

    def test_fast_apply_uses_current_male_protagonist_instance(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(
                2,
                [item(11, "module"), item(22, "core")],
                characters=[
                    {"character_id": 1046, "uid": {"slot": 104, "serial": 600}},
                ],
            )
        )
        service = EquipmentApplyService(self.dao, FakeSyncService(self.dao, snapshot_id))

        self.assertEqual(
            1046,
            service.resolve_fast_apply_character_id(1051, snapshot_id),
        )
        self.assertEqual(
            {"slot": 104, "serial": 600},
            service.resolve_character_uid(1046, snapshot_id),
        )

    def test_fast_apply_protagonist_preference_validates_snapshot_presence(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(
                2,
                [item(11, "module"), item(22, "core")],
                characters=[
                    {"character_id": 1046, "uid": {"slot": 104, "serial": 600}},
                    {"character_id": 1051, "uid": {"slot": 105, "serial": 601}},
                ],
            )
        )
        service = EquipmentApplyService(self.dao, FakeSyncService(self.dao, snapshot_id))

        self.assertEqual(1051, service.resolve_fast_apply_character_id(1051, snapshot_id))
        self.assertEqual(
            (1051, 1046),
            service.resolve_fast_apply_character_ids(1051, snapshot_id),
        )
        self.assertEqual(
            1046,
            service.resolve_fast_apply_character_id(
                1051, snapshot_id, protagonist_target="male"
            ),
        )
    def tearDown(self) -> None:
        self.dao.close()
        self.temp_dir.cleanup()

    def test_dispatches_native_uids_and_verifies_new_snapshot(self) -> None:
        result = EquipmentApplyService(self.dao, self.sync).apply_plan(self.plan_id)

        self.assertTrue(result.verified)
        self.assertFalse(result.already_applied)
        self.assertGreater(result.after_snapshot_id, result.before_snapshot_id)
        self.assertEqual(self.sync.params["character"], CHARACTER_UID)
        self.assertEqual(
            self.sync.params["placements"],
            [{"equipment": {"slot": 11, "serial": 11}, "row": 2, "column": 3}],
        )
        self.assertEqual(self.sync.params["core"], {"slot": 22, "serial": 22})

    def test_already_applied_plan_returns_immediately_without_rpc(self) -> None:
        rows = [copy.deepcopy(item(11, "module")), copy.deepcopy(item(22, "core"))]
        for row in rows:
            row["equipped"] = True
            row["equipped_character_uid"] = dict(CHARACTER_UID)
            row["equipped_character_id"] = 1003
            if row["kind"] == "module":
                row["equipped_placement"] = {"row": 2, "column": 3}
        current = self.dao.import_inventory_snapshot(snapshot(3, rows))
        self.sync.state = InventorySyncState(
            phase="listening",
            running=True,
            last_snapshot_id=current,
            last_item_count=2,
        )

        result = EquipmentApplyService(self.dao, self.sync).apply_plan(self.plan_id)

        self.assertTrue(result.verified)
        self.assertTrue(result.already_applied)
        self.assertEqual(result.before_snapshot_id, result.after_snapshot_id)
        self.assertEqual(result.rpc_result, {"status": "already_applied"})
        self.assertIsNone(self.sync.params)

    def test_verify_plan_in_snapshot_is_read_only(self) -> None:
        rows = [copy.deepcopy(item(11, "module")), copy.deepcopy(item(22, "core"))]
        for row in rows:
            row["equipped"] = True
            row["equipped_character_uid"] = dict(CHARACTER_UID)
            row["equipped_character_id"] = 1003
            if row["kind"] == "module":
                row["equipped_placement"] = {"row": 2, "column": 3}
        current = self.dao.import_inventory_snapshot(snapshot(3, rows))

        mismatch = EquipmentApplyService(
            self.dao, self.sync
        ).verify_plan_in_snapshot(
            self.plan_id,
            character_uid=CHARACTER_UID,
            target_character_id=1003,
            stable_snapshot_id=current,
            exact_loadout=True,
        )

        self.assertIsNone(mismatch)
        self.assertIsNone(self.sync.params)
        self.assertEqual([], self.sync.module_calls)
        self.assertEqual([], self.sync.unmount_calls)

    def test_verify_plan_in_snapshot_reports_mismatch_without_repairing(self) -> None:
        current = self.dao.import_inventory_snapshot(
            snapshot(3, [item(11, "module"), item(22, "core")])
        )

        mismatch = EquipmentApplyService(
            self.dao, self.sync
        ).verify_plan_in_snapshot(
            self.plan_id,
            character_uid=CHARACTER_UID,
            target_character_id=1003,
            stable_snapshot_id=current,
            exact_loadout=True,
        )

        self.assertIn("装配位置不一致", mismatch)
        self.assertIsNone(self.sync.params)
        self.assertEqual([], self.sync.module_calls)
        self.assertEqual([], self.sync.unmount_calls)

    def test_driver_only_plan_keeps_existing_core_and_dispatches_module_move(self) -> None:
        plan_id = self.dao.save_loadout_plan(
            name="仅驱动装配测试",
            character_id=1003,
            source_snapshot_id=1,
            status="ready",
            assignments=[
                {
                    "uid_serial": 11,
                    "uid_slot": 11,
                    "kind": "module",
                    "target_row": 2,
                    "target_column": 3,
                    "rotation": 0,
                },
            ],
        )

        result = EquipmentApplyService(self.dao, self.sync).apply_plan(plan_id)

        self.assertTrue(result.verified)
        self.assertEqual(self.sync.params, None)
        self.assertEqual(
            self.sync.module_calls,
            [{
                "character": CHARACTER_UID,
                "equipment": {"slot": 11, "serial": 11},
                "row": 2,
                "column": 3,
            }],
        )

    def test_legacy_incomplete_driver_only_plan_is_fast_apply_eligible(self) -> None:
        plan_id = self.dao.save_loadout_plan(
            name="旧版无卡带方案",
            character_id=1003,
            source_snapshot_id=1,
            status="incomplete",
            assignments=[
                {
                    "uid_serial": 11,
                    "uid_slot": 11,
                    "kind": "module",
                    "target_row": 2,
                    "target_column": 3,
                    "rotation": 0,
                },
            ],
        )

        service = EquipmentApplyService(self.dao, self.sync)
        service.validate_bulk_plans_for_fast_apply(
            [{
                "role_name": "旧版无卡带方案",
                "plan_id": plan_id,
                "character_uid": CHARACTER_UID,
            }],
            stable_snapshot_id=self.sync.state.last_snapshot_id,
        )
        result = service.apply_plan(plan_id)

        self.assertTrue(result.verified)
        self.assertIsNone(self.sync.params)
        self.assertEqual(["move"], self.sync.module_dispatches)

    def test_fast_dispatch_does_not_wait_for_a_new_inventory_snapshot(self) -> None:
        self.sync.emit_snapshot = False

        with patch("src.services.equipment_apply_service.time.sleep") as sleep:
            result = EquipmentApplyService(self.dao, self.sync).apply_plan(
                self.plan_id,
                verify_after_dispatch=False,
            )

        self.assertFalse(result.verified)
        self.assertEqual(result.before_snapshot_id, result.after_snapshot_id)
        self.assertEqual(self.sync.wait_calls, 0)
        self.assertEqual(self.sync.params["character"], CHARACTER_UID)
        self.assertEqual([0.5], [row.args[0] for row in sleep.call_args_list])

    def test_fast_reset_unmounts_mismatched_role_before_one_key_dispatch(self) -> None:
        self.sync.emit_snapshot = False

        with patch("src.services.equipment_apply_service.time.sleep") as sleep:
            result = EquipmentApplyService(self.dao, self.sync).apply_plan(
                self.plan_id,
                verify_after_dispatch=False,
                exact_loadout=True,
                force_dispatch=True,
                reset_before_apply=True,
            )

        self.assertFalse(result.verified)
        self.assertEqual(["all"], [name for name, _ in self.sync.unmount_calls])
        self.assertEqual(CHARACTER_UID, self.sync.params["character"])
        self.assertEqual([0.7, 0.5], [row.args[0] for row in sleep.call_args_list])

    def test_fast_reset_rebuilds_a_mismatched_driver_only_plan(self) -> None:
        plan_id = self.dao.save_loadout_plan(
            name="仅驱动极速重装测试",
            character_id=1003,
            source_snapshot_id=1,
            status="ready",
            assignments=[{
                "uid_serial": 11,
                "uid_slot": 11,
                "kind": "module",
                "target_row": 2,
                "target_column": 3,
                "rotation": 0,
            }],
        )
        self.sync.emit_snapshot = False

        with patch("src.services.equipment_apply_service.time.sleep") as sleep:
            result = EquipmentApplyService(self.dao, self.sync).apply_plan(
                plan_id,
                verify_after_dispatch=False,
                exact_loadout=True,
                force_dispatch=True,
                reset_before_apply=True,
            )

        self.assertFalse(result.verified)
        self.assertEqual(["all"], [name for name, _ in self.sync.unmount_calls])
        self.assertEqual(["equip"], self.sync.module_dispatches)
        self.assertEqual([0.7, 0.5], [row.args[0] for row in sleep.call_args_list])
        self.assertEqual(
            {"row": 2, "column": 3},
            {
                "row": self.sync.module_calls[0]["row"],
                "column": self.sync.module_calls[0]["column"],
            },
        )

    def test_pinned_snapshot_allows_later_fast_dispatch_after_listener_changes(self) -> None:
        before_snapshot_id = self.sync.state.last_snapshot_id
        self.sync.state = InventorySyncState(
            phase="capturing",
            running=True,
            last_snapshot_id=before_snapshot_id,
            last_item_count=2,
        )

        result = EquipmentApplyService(self.dao, self.sync).apply_plan(
            self.plan_id,
            stable_snapshot_id=before_snapshot_id,
            verify_after_dispatch=False,
        )

        self.assertFalse(result.verified)
        self.assertEqual(result.before_snapshot_id, before_snapshot_id)

    def test_resolves_uid_from_current_snapshot_character_list_when_character_is_empty(self) -> None:
        current = self.dao.import_inventory_snapshot(
            snapshot(
                4, [item(11, "module"), item(22, "core")],
                characters=[{"character_id": 1003, "uid": CHARACTER_UID}],
            )
        )

        resolved = EquipmentApplyService(
            self.dao, self.sync
        ).resolve_character_uid(1003, current)

        self.assertEqual(resolved, CHARACTER_UID)

    def test_resolves_uid_from_persisted_manual_mapping_when_no_equipment_exists(self) -> None:
        self.dao.upsert_character_instance_mapping(2000, {"slot": 300, "serial": 301})
        current = self.dao.import_inventory_snapshot(
            snapshot(5, [item(11, "module"), item(22, "core")])
        )
        self.assertEqual(
            {"slot": 300, "serial": 301},
            EquipmentApplyService(self.dao, self.sync).resolve_character_uid(2000, current),
        )

    def test_manual_instance_mapping_takes_priority_over_history(self) -> None:
        self.dao.upsert_character_instance_mapping(1003, {"slot": 300, "serial": 301})
        current = self.dao.import_inventory_snapshot(
            snapshot(5, [item(11, "module"), item(22, "core")])
        )

        self.assertEqual(
            {"slot": 300, "serial": 301},
            EquipmentApplyService(self.dao, self.sync).resolve_character_uid(1003, current),
        )

    def test_resolves_uid_from_account_snapshot_cache_when_current_core_event_omits_role(self) -> None:
        """A shortened nte-core ``characters`` list must not discard a known account UID."""

        self.dao.import_inventory_snapshot(
            snapshot(
                5, [item(11, "module"), item(22, "core")],
                characters=[{"character_id": 2000, "uid": {"slot": 300, "serial": 301}}],
            )
        )
        current = self.dao.import_inventory_snapshot(
            snapshot(
                6, [item(11, "module"), item(22, "core")],
                characters=[{"character_id": 1003, "uid": CHARACTER_UID}],
            )
        )

        self.assertEqual(
            {"slot": 300, "serial": 301},
            EquipmentApplyService(self.dao, self.sync).resolve_character_uid(2000, current),
        )

    def test_rejects_missing_character_instance_when_account_cache_is_empty(self) -> None:
        current = self.dao.import_inventory_snapshot(
            snapshot(5, [item(11, "module"), item(22, "core")])
        )
        with self.assertRaisesRegex(EquipmentApplyError, "角色实例缓存"):
            EquipmentApplyService(self.dao, self.sync).resolve_character_uid(2000, current)

    def test_current_snapshot_character_uid_beats_manual_fallback(self) -> None:
        self.dao.upsert_character_instance_mapping(1003, {"slot": 300, "serial": 301})
        current = self.dao.import_inventory_snapshot(
            snapshot(
                5, [item(11, "module"), item(22, "core")],
                characters=[{"character_id": 1003, "uid": CHARACTER_UID}],
            )
        )
        self.assertEqual(
            CHARACTER_UID,
            EquipmentApplyService(self.dao, self.sync).resolve_character_uid(1003, current),
        )

    def test_rejects_missing_equipment_capability_before_rpc(self) -> None:
        self.sync.core_hello_result = {"capabilities": ["inventory"]}
        with self.assertRaisesRegex(EquipmentApplyError, "equipment"):
            EquipmentApplyService(self.dao, self.sync).apply_plan(self.plan_id)
        self.assertIsNone(self.sync.params)

    def test_rejects_virtual_incomplete_plan_before_any_equipment_rpc(self) -> None:
        plan_id = self.dao.save_loadout_plan(
            name="虚拟补位方案",
            character_id=1003,
            source_snapshot_id=1,
            status="incomplete",
            assignments=[{
                "uid_serial": 6_247_664_025_326_415_460,
                "uid_slot": 0,
                "kind": "module",
                "target_row": 2,
                "target_column": 1,
                "rotation": 0,
                "virtual": True,
            }],
        )

        with self.assertRaisesRegex(EquipmentApplyError, "虚拟补位驱动"):
            EquipmentApplyService(self.dao, self.sync).apply_plan(plan_id)
        self.assertIsNone(self.sync.params)

    def test_rejects_snapshot_that_does_not_confirm_target_position(self) -> None:
        self.sync.verify_correctly = False
        with self.assertRaisesRegex(EquipmentApplyError, "装配位置不一致"):
            EquipmentApplyService(self.dao, self.sync).apply_plan(self.plan_id)

    def test_bulk_validation_rejects_equipment_uid_conflict(self) -> None:
        duplicate_plan_id = self.dao.save_loadout_plan(
            name="冲突方案",
            character_id=2000,
            source_snapshot_id=1,
            status="ready",
            assignments=[{
                "uid_serial": 11,
                "uid_slot": 11,
                "kind": "module",
                "target_row": 1,
                "target_column": 1,
                "rotation": 0,
            }],
        )
        service = EquipmentApplyService(self.dao, self.sync)

        with self.assertRaisesRegex(EquipmentApplyError, "方案冲突"):
            service.validate_bulk_plans_for_fast_apply(
                [
                    {
                        "role_name": "甲",
                        "plan_id": self.plan_id,
                        "character_uid": CHARACTER_UID,
                    },
                    {
                        "role_name": "乙",
                        "plan_id": duplicate_plan_id,
                        "character_uid": {"slot": 8, "serial": 800},
                    },
                ],
                stable_snapshot_id=self.sync.state.last_snapshot_id,
            )

if __name__ == "__main__":
    unittest.main()
