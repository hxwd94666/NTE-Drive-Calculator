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

    def test_apply_uses_nte_core_state_rpcs_without_waiting_for_a_new_snapshot(self):
        from src.services.warehouse_state_management import (
            WarehouseStateManagementPlan,
            WarehouseStateManagementService,
        )

        before_rows = [
            {"uid_slot": 1, "uid_serial": 10, "locked": False, "discarded": False},
            {"uid_slot": 2, "uid_serial": 20, "locked": True, "discarded": False},
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
                return before_rows

        dao = Dao()

        class Sync:
            is_running = True
            state = SimpleNamespace(phase="listening")
            core_hello_result = {"capabilities": ["equipment"]}

            def __init__(self):
                self.calls = []

            def set_item_discarded(self, *, equipment, discarded):
                self.calls.append(("discarded", equipment, discarded))

            def set_item_locked(self, *, equipment, locked):
                self.calls.append(("locked", equipment, locked))

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

        result = service.apply(plan)

        self.assertEqual(
            [
                ("discarded", {"slot": 1, "serial": 10}, True),
                ("locked", {"slot": 2, "serial": 20}, False),
            ],
            sync.calls,
        )
        self.assertEqual(7, result.before_snapshot_id)
        self.assertEqual(1, result.summary["discard_set_count"])
        self.assertEqual(1, result.summary["lock_clear_count"])


if __name__ == "__main__":
    unittest.main()
