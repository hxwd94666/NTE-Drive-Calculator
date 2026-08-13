# 验证配装锁定在不同背包来源和保存边界上的保留语义。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.services.allocation_lock_service import (
    build_allocation_lock_snapshot,
    filter_allocation_request_for_locks,
    verify_allocation_lock_snapshot,
)
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from tests.test_user_data_loadout_dao import item, snapshot


def _official_payload(role_name: str) -> dict[str, str]:
    return {
        "schema": "allocation-official-snapshot-v1",
        "source_role_name": role_name,
    }


def _game_payload(role_name: str) -> dict[str, str]:
    return {
        "schema": "game-observed-loadout-v1",
        "source_role_name": role_name,
    }


def _module(serial: int, slot: int) -> dict[str, int | str]:
    return {
        "uid_serial": serial,
        "uid_slot": slot,
        "kind": "module",
        "target_row": 1,
        "target_column": 1,
        "rotation": 0,
    }


def _core(serial: int, slot: int) -> dict[str, int | str]:
    return {
        "uid_serial": serial,
        "uid_slot": slot,
        "kind": "core",
        "rotation": 0,
    }


class AllocationLockServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "user.sqlite3"
        self.dao = UserDataDao(self.path, account_id="default")

    def tearDown(self) -> None:
        self.dao.close()
        self.temp_dir.cleanup()

    def _save_lockable_plan(self, snapshot_id: int, *, role_name: str = "早雾") -> int:
        return self.dao.save_loadout_plan(
            name=f"{role_name} 方案",
            character_id=1003,
            source_snapshot_id=snapshot_id,
            status="incomplete",
            is_active=True,
            assignments=[_module(11, 22), _core(12, 23)],
            payload=_official_payload(role_name),
        )

    def test_lock_reserves_same_native_uids_for_gamepad_current_snapshot(self) -> None:
        nte_snapshot = self.dao.import_inventory_snapshot(
            snapshot(1, [item(11, 22), item(12, 23, "core")]),
            source="nte_core",
        )
        plan_id = self._save_lockable_plan(nte_snapshot)
        self.assertTrue(self.dao.set_allocation_lock(plan_id, True))
        gamepad_snapshot = self.dao.import_inventory_snapshot(
            snapshot(2, [item(11, 22), item(12, 23, "core")]),
            source="gamepad",
        )

        lock_snapshot = build_allocation_lock_snapshot(
            self.dao,
            inventory_snapshot_id=gamepad_snapshot,
        )

        self.assertEqual(frozenset({"早雾"}), lock_snapshot.locked_role_names)
        self.assertEqual(
            frozenset({"nte-module-22-11", "nte-core-23-12"}),
            lock_snapshot.reserved_uids,
        )
        roles, groups = filter_allocation_request_for_locks(
            ["早雾", "千秋", "岚"],
            [["早雾", "千秋"], ["岚"]],
            lock_snapshot,
        )
        self.assertEqual(["千秋", "岚"], roles)
        self.assertEqual([["千秋"], ["岚"]], groups)
        verify_allocation_lock_snapshot(self.dao, lock_snapshot)

    def test_lock_accepts_missing_core_and_rejects_virtual_assignments(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(1, [item(11, 22), item(12, 23, "core")])
        )
        no_core_plan = self.dao.save_loadout_plan(
            name="无卡带",
            character_id=1003,
            source_snapshot_id=snapshot_id,
            is_active=True,
            assignments=[_module(11, 22)],
            payload=_official_payload("早雾"),
        )
        self.assertTrue(self.dao.set_allocation_lock(no_core_plan, True))
        lock_snapshot = build_allocation_lock_snapshot(
            self.dao,
            inventory_snapshot_id=snapshot_id,
        )
        self.assertEqual(frozenset({"早雾"}), lock_snapshot.locked_role_names)
        self.assertEqual(
            frozenset({"nte-module-22-11"}),
            lock_snapshot.reserved_uids,
        )

        self.dao.set_allocation_lock(no_core_plan, False)
        self.dao.deactivate_loadout_plan(no_core_plan)
        virtual_core = _core(99, 0)
        virtual_core["virtual"] = True
        virtual_plan = self.dao.save_loadout_plan(
            name="虚拟卡带",
            character_id=1003,
            source_snapshot_id=snapshot_id,
            is_active=True,
            assignments=[_module(11, 22), virtual_core],
            payload=_official_payload("早雾"),
        )
        with self.assertRaisesRegex(UserDataValidationError, "虚拟"):
            self.dao.set_allocation_lock(virtual_plan, True)

    def test_imported_game_plan_is_a_lockable_calculation_reservation(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(1, [item(11, 22), item(12, 23, "core")]),
            source="nte_core",
        )
        plan_id = self.dao.save_loadout_plan(
            name="游戏导入方案",
            character_id=1003,
            source_snapshot_id=snapshot_id,
            status="incomplete",
            is_active=True,
            assignments=[_module(11, 22)],
            payload=_game_payload("早雾"),
        )

        self.assertTrue(self.dao.set_allocation_lock(plan_id, True))
        listed = self.dao.list_allocation_locked_loadout_plans_by_role()
        self.assertEqual(plan_id, listed["早雾"]["plan_id"])
        lock_snapshot = build_allocation_lock_snapshot(
            self.dao,
            inventory_snapshot_id=snapshot_id,
        )
        self.assertEqual(
            frozenset({"nte-module-22-11"}),
            lock_snapshot.reserved_uids,
        )

    def test_lock_blocks_overwrite_and_equipment_borrow(self) -> None:
        snapshot_id = self.dao.import_inventory_snapshot(
            snapshot(1, [item(11, 22), item(12, 23, "core"), item(13, 24)])
        )
        plan_id = self._save_lockable_plan(snapshot_id)
        self.dao.set_allocation_lock(plan_id, True)

        with self.assertRaisesRegex(UserDataValidationError, "锁定方案"):
            self.dao.replace_active_loadout_plans([{
                "name": "覆盖锁定角色",
                "character_id": 1003,
                "source_snapshot_id": snapshot_id,
                "assignments": [_module(13, 24), _core(12, 23)],
            }])
        with self.assertRaisesRegex(UserDataValidationError, "不能借用锁定"):
            self.dao.replace_active_loadout_plans([{
                "name": "借用锁定装备",
                "character_id": 1055,
                "source_snapshot_id": snapshot_id,
                "assignments": [_module(11, 22)],
            }])

    def test_stale_locked_uid_blocks_calculation_and_lock_revision_blocks_save(self) -> None:
        first_snapshot = self.dao.import_inventory_snapshot(
            snapshot(1, [item(11, 22), item(12, 23, "core")])
        )
        plan_id = self._save_lockable_plan(first_snapshot)
        self.dao.set_allocation_lock(plan_id, True)
        lock_snapshot = build_allocation_lock_snapshot(
            self.dao,
            inventory_snapshot_id=first_snapshot,
        )
        self.dao.set_allocation_lock(plan_id, False)
        with self.assertRaisesRegex(UserDataValidationError, "计算期间变化"):
            verify_allocation_lock_snapshot(self.dao, lock_snapshot)

        self.dao.set_allocation_lock(plan_id, True)
        second_snapshot = self.dao.import_inventory_snapshot(
            snapshot(2, [item(13, 24), item(12, 23, "core")])
        )
        with self.assertRaisesRegex(UserDataValidationError, "不在当前稳定背包快照"):
            build_allocation_lock_snapshot(
                self.dao,
                inventory_snapshot_id=second_snapshot,
            )


if __name__ == "__main__":
    unittest.main()
