# 校验仓库状态管理通过本地核心组件写回并等待新快照确认。
import unittest
from types import SimpleNamespace


class WarehouseStateManagementTests(unittest.TestCase):
    def test_manual_plan_uses_pinned_snapshot_and_omits_unchanged_target(self):
        from src.services.warehouse_state_management import WarehouseStateManagementService

        rows = [
            {"kind": "module", "uid_slot": 1, "uid_serial": 10, "locked": False, "discarded": False},
            {"kind": "core", "uid_slot": 2, "uid_serial": 20, "locked": True, "discarded": False},
        ]

        class Dao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def current_inventory_snapshot_id(self):
                return 7

            def list_inventory_items(self, snapshot_id):
                if snapshot_id != 7:
                    raise AssertionError("unexpected snapshot")
                return rows

        dao = Dao()
        service = WarehouseStateManagementService("unused.sqlite3", object(), dao_factory=lambda _path: dao)

        plan = service.plan_manual_changes(
            7,
            {"nte-module-1-10": "discarded", "nte-core-2-20": "locked"},
        )

        self.assertEqual(7, plan.snapshot_id)
        self.assertEqual(1, len(plan.changes))
        self.assertEqual({"slot": 1, "serial": 10}, plan.changes[0]["equipment"])
        self.assertEqual("discarded", plan.changes[0]["target_state"])

    def test_apply_waits_through_intermediate_snapshot_until_changes_match(self):
        from src.services.warehouse_state_management import (
            WarehouseStateManagementPlan,
            WarehouseStateManagementService,
        )

        before_rows = [
            {"uid_slot": 1, "uid_serial": 10, "locked": False, "discarded": False},
            {"uid_slot": 2, "uid_serial": 20, "locked": True, "discarded": False},
        ]
        intermediate_rows = [
            {"uid_slot": 1, "uid_serial": 10, "locked": False, "discarded": True},
            {"uid_slot": 2, "uid_serial": 20, "locked": True, "discarded": False},
        ]
        confirmed_rows = [
            {"uid_slot": 1, "uid_serial": 10, "locked": False, "discarded": True},
            {"uid_slot": 2, "uid_serial": 20, "locked": False, "discarded": False},
        ]

        class Dao:
            snapshot_id = 7

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def current_inventory_snapshot_id(self):
                return self.snapshot_id

            def list_inventory_items(self, snapshot_id):
                return {
                    7: before_rows,
                    8: intermediate_rows,
                    9: confirmed_rows,
                }[snapshot_id]

        dao = Dao()

        class Sync:
            is_running = True
            state = SimpleNamespace(phase="listening")
            core_hello_result = {"capabilities": ["equipment"]}

            def __init__(self):
                self.calls = []
                self.snapshot_waits = []

            def set_item_discarded(self, *, equipment, discarded):
                self.calls.append(("discarded", equipment, discarded))

            def set_item_locked(self, *, equipment, locked):
                self.calls.append(("locked", equipment, locked))

            def wait_for_snapshot(self, *, after_snapshot_id, timeout):
                self.snapshot_waits.append(after_snapshot_id)
                next_snapshot_id = {7: 8, 8: 9}[after_snapshot_id]
                dao.snapshot_id = next_snapshot_id
                return SimpleNamespace(last_snapshot_id=next_snapshot_id)

        sync = Sync()
        service = WarehouseStateManagementService("unused.sqlite3", sync, dao_factory=lambda _path: dao)
        plan = WarehouseStateManagementPlan(
            snapshot_id=7,
            changes=(
                {"equipment": {"slot": 1, "serial": 10}, "target_state": "discarded", "current_state": "normal"},
                {"equipment": {"slot": 2, "serial": 20}, "target_state": "normal", "current_state": "locked"},
            ),
            filter_summary={},
        )

        progress_messages = []
        result = service.apply(
            plan,
            progress_callback=progress_messages.append,
        )

        self.assertEqual(
            [
                ("discarded", {"slot": 1, "serial": 10}, True),
                ("locked", {"slot": 2, "serial": 20}, False),
            ],
            sync.calls,
        )
        self.assertEqual(7, result.before_snapshot_id)
        self.assertEqual(9, result.after_snapshot_id)
        self.assertTrue(result.verified)
        self.assertIsNone(result.verification_error)
        self.assertEqual([7, 8], sync.snapshot_waits)
        self.assertTrue(
            any("第 1/2 件" in message for message in progress_messages)
        )
        self.assertTrue(
            any("新快照 #9 已确认" in message for message in progress_messages)
        )
        self.assertEqual(1, result.summary["discard_set_count"])
        self.assertEqual(1, result.summary["lock_clear_count"])
        self.assertEqual(
            (
                {"uid": "nte-core-1-10", "target_state": "discarded", "current_state": "normal", "equipment": {"slot": 1, "serial": 10}},
                {"uid": "nte-core-2-20", "target_state": "normal", "current_state": "locked", "equipment": {"slot": 2, "serial": 20}},
            ),
            result.changes,
        )

    def test_apply_preserves_dispatched_result_when_snapshot_confirmation_times_out(self):
        from src.services.warehouse_state_management import (
            WarehouseStateManagementPlan,
            WarehouseStateManagementService,
        )

        rows = [
            {
                "uid_slot": 1,
                "uid_serial": 10,
                "locked": False,
                "discarded": False,
            },
        ]

        class Dao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def current_inventory_snapshot_id(self):
                return 7

            def list_inventory_items(self, snapshot_id):
                self.snapshot_id = snapshot_id
                return rows

        class Sync:
            is_running = True
            state = SimpleNamespace(phase="listening")
            core_hello_result = {"capabilities": ["equipment"]}

            def __init__(self):
                self.calls = []

            def set_item_discarded(self, *, equipment, discarded):
                self.calls.append((equipment, discarded))

            def set_item_locked(self, *, equipment, locked):
                raise AssertionError("unexpected lock RPC")

            def wait_for_snapshot(self, *, after_snapshot_id, timeout):
                raise TimeoutError("test timeout")

        sync = Sync()
        service = WarehouseStateManagementService(
            "unused.sqlite3",
            sync,
            dao_factory=lambda _path: Dao(),
        )
        plan = WarehouseStateManagementPlan(
            snapshot_id=7,
            changes=(
                {
                    "equipment": {"slot": 1, "serial": 10},
                    "target_state": "discarded",
                    "current_state": "normal",
                },
            ),
            filter_summary={},
        )

        result = service.apply(plan, confirmation_timeout=0.01)

        self.assertEqual(
            [({"slot": 1, "serial": 10}, True)],
            sync.calls,
        )
        self.assertIsNone(result.after_snapshot_id)
        self.assertFalse(result.verified)
        self.assertIn("未在限定时间", result.verification_error or "")
        self.assertEqual("discarded", result.changes[0]["target_state"])


if __name__ == "__main__":
    unittest.main()
