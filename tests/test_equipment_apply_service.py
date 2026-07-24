# 测试一键装配的能力检查、参数派发和新稳定快照验证。
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from src.services.character_instance_cache import CharacterInstanceCache
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


def snapshot(generation: int, items: list[dict]) -> dict:
    return {
        "method": "event.inventory.snapshot",
        "params": {
            "complete": True,
            "generation": generation,
            "sequence": generation,
            "observed_at_unix_ms": 1_800_000_000_000 + generation,
            "item_count": len(items),
            "items": items,
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
        return self._apply_module(**kwargs)

    def move_module_to_character(self, **kwargs):
        return self._apply_module(**kwargs)

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

    def test_fast_dispatch_does_not_wait_for_a_new_inventory_snapshot(self) -> None:
        self.sync.emit_snapshot = False

        result = EquipmentApplyService(self.dao, self.sync).apply_plan(
            self.plan_id,
            verify_after_dispatch=False,
        )

        self.assertFalse(result.verified)
        self.assertEqual(result.before_snapshot_id, result.after_snapshot_id)
        self.assertEqual(self.sync.wait_calls, 0)
        self.assertEqual(self.sync.params["character"], CHARACTER_UID)

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

    def test_resolves_uid_from_history_when_character_is_currently_empty(self) -> None:
        current = self.dao.import_inventory_snapshot(
            snapshot(4, [item(11, "module"), item(22, "core")])
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

    def test_resolves_uid_from_account_scoped_public_instance_cache(self) -> None:
        cache_path = Path(self.temp_dir.name) / "public-instance-cache.sqlite3"
        with CharacterInstanceCache(cache_path) as cache:
            cache.upsert("apply-test", 2000, {"slot": 300, "serial": 301}, source="snapshot")
        current = self.dao.import_inventory_snapshot(
            snapshot(5, [item(11, "module"), item(22, "core")])
        )
        self.assertEqual(
            {"slot": 300, "serial": 301},
            EquipmentApplyService(
                self.dao, self.sync, instance_cache_path=str(cache_path),
            ).resolve_character_uid(2000, current),
        )

    def test_existing_private_mapping_is_mirrored_to_public_instance_cache(self) -> None:
        cache_path = Path(self.temp_dir.name) / "public-instance-cache.sqlite3"
        service = EquipmentApplyService(
            self.dao, self.sync, instance_cache_path=str(cache_path),
        )
        service.resolve_character_uid(1003, self.sync.state.last_snapshot_id)
        with CharacterInstanceCache(cache_path) as cache:
            self.assertEqual(CHARACTER_UID, cache.get("apply-test", 1003))

    def test_rejects_missing_equipment_capability_before_rpc(self) -> None:
        self.sync.core_hello_result = {"capabilities": ["inventory"]}
        with self.assertRaisesRegex(EquipmentApplyError, "equipment"):
            EquipmentApplyService(self.dao, self.sync).apply_plan(self.plan_id)
        self.assertIsNone(self.sync.params)

    def test_rejects_snapshot_that_does_not_confirm_target_position(self) -> None:
        self.sync.verify_correctly = False
        with self.assertRaisesRegex(EquipmentApplyError, "装配位置不一致"):
            EquipmentApplyService(self.dao, self.sync).apply_plan(self.plan_id)


if __name__ == "__main__":
    unittest.main()
