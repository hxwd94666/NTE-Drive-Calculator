# 验证仓库状态批次的边界、旧组件兼容、参数校验和未知结果停止。
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from src.integrations.nte_core import NteCoreClient
from src.integrations.nte_core_equipment import STATE_BATCH_CAPABILITY, state_batch_params
from src.integrations.nte_core_protocol import NteCoreRpcError
from src.integrations.warehouse_state_writer import WarehouseStateWriter


class Sync:
    def __init__(self, batch=True):
        self.core_hello_result = {"capabilities": [STATE_BATCH_CAPABILITY] if batch else ["equipment"]}
        self.calls, self.progress = [], []
        self.active = False
        self.error = None

    @contextmanager
    def equipment_batch(self):
        self.active = True
        try:
            yield self
        finally:
            self.active = False

    def set_item_states(self, *, operations):
        assert self.active
        self.calls.append(operations)
        if self.error:
            error, self.error = self.error, None
            raise error
        return {"status": "rpc_dispatched", "confirmed": False}

    def set_item_locked(self, **kwargs):
        assert self.active
        self.calls.append(("locked", kwargs))

    def set_item_discarded(self, **kwargs):
        assert self.active
        self.calls.append(("discarded", kwargs))


def changes(count, locked=False):
    return [({"locked": locked}, "discarded", {"slot": 8, "serial": i + 1}) for i in range(count)]


class WarehouseStateBatchTests(unittest.TestCase):
    def test_chunk_budget_and_progress_do_not_split_unlock_discard_pairs(self):
        sync = Sync()
        metrics = WarehouseStateWriter(sync).apply_many(changes(17, True), group_completed=lambda *x: sync.progress.append(x))
        self.assertEqual([16, 16, 2], [len(x) for x in sync.calls])
        self.assertEqual([(8, 17), (16, 17), (17, 17)], sync.progress)
        for batch in sync.calls:
            for unlock, discard in zip(batch[::2], batch[1::2]):
                self.assertEqual(unlock["equipment"], discard["equipment"])
                self.assertEqual(("locked", False), (unlock["field"], unlock["value"]))
                self.assertEqual(("discarded", True), (discard["field"], discard["value"]))
        self.assertEqual(3, metrics.rpc_count)
        self.assertFalse(sync.active)

    def test_legacy_provider_uses_existing_ordered_single_calls(self):
        sync = Sync(False)
        metrics = WarehouseStateWriter(sync).apply_many(changes(2, True))
        self.assertEqual(["locked", "discarded", "locked", "discarded"], [x[0] for x in sync.calls])
        self.assertEqual(4, metrics.rpc_count)

    def test_partial_or_timeout_never_replays_batch_or_sends_next_chunk(self):
        for error in [NteCoreRpcError({"code": -32001, "message": "control_timeout"}),
                      NteCoreRpcError({"code": -32001, "data": {"domain_code": "EQUIPMENT_OUTCOME_UNKNOWN"}})]:
            sync = Sync()
            sync.error = error
            with self.assertRaises(NteCoreRpcError), patch("src.integrations.warehouse_state_writer.sleep") as sleeper:
                WarehouseStateWriter(sync).apply_many(changes(40))
            self.assertEqual(1, len(sync.calls))
            self.assertFalse(sync.active)
            sleeper.assert_not_called()

    def test_pre_dispatch_busy_retries_only_rejected_chunk(self):
        sync = Sync()
        sync.error = NteCoreRpcError({"code": -32001, "data": {"domain_code": "EQUIPMENT_PLUGIN_BUSY"}})
        with patch("src.integrations.warehouse_state_writer.sleep"):
            metrics = WarehouseStateWriter(sync).apply_many(changes(17))
        self.assertEqual([16, 16, 1], [len(x) for x in sync.calls])
        self.assertEqual(sync.calls[0], sync.calls[1])
        self.assertEqual(1, metrics.retry_count)

    def test_noop_groups_do_not_send_empty_batch(self):
        sync = Sync()
        result = WarehouseStateWriter(sync).apply_many([({"discarded": True}, "discarded", {"slot": 8, "serial": 1})])
        self.assertEqual([], sync.calls)
        self.assertEqual(0, result.rpc_count)

    def test_client_validates_entire_batch_before_any_rpc(self):
        good = {"equipment": {"slot": 8, "serial": 1}, "field": "locked", "value": True}
        for invalid in [[], [good] * 17, [good, {**good, "value": 1}],
                        [good, {**good, "field": "unknown"}], [good, {**good, "extra": 1}]]:
            with self.assertRaises(ValueError):
                state_batch_params(invalid)
        class Client:
            def _equipment_request(self, method, params):
                self.method, self.params = method, params
                return {"confirmed": False}
        client = Client()
        self.assertEqual({"confirmed": False}, NteCoreClient.set_item_states(client, operations=[good]))
        self.assertEqual("set_item_states", client.method)
        self.assertEqual({"operations": [good]}, client.params)

    def test_account_change_after_first_chunk_stops_remaining_dispatch(self):
        from types import SimpleNamespace
        from src.services.native_game_session import NativeGameSession
        from src.services.inventory_capture_wait import InventorySyncCancelled
        from tests.test_native_equipment_session import EquipmentCore

        core = EquipmentCore()
        core.hello_result["capabilities"].append(STATE_BATCH_CAPABILITY)
        generation = [1]
        calls = []
        def dispatch(**kwargs):
            calls.append(kwargs)
            generation[0] += 1
            return {"status": "rpc_dispatched", "confirmed": False}
        core.set_item_states = dispatch
        session = NativeGameSession(lambda: core, lambda _cap: None, context_key=lambda: generation[0])
        lease = session.inventory_client()
        lease.start_capture(profile="inventory")
        try:
            self.assertTrue(lease.status()["native_snapshot_ready"])
            sync = SimpleNamespace(core_hello_result=core.hello_result,
                                   equipment_batch=lease.equipment_batch, set_item_states=lease.set_item_states)
            with self.assertRaises(InventorySyncCancelled):
                WarehouseStateWriter(sync).apply_many(changes(17))
            self.assertEqual(1, len(calls))
            self.assertFalse(lease.snapshot_ready)
        finally:
            lease.close()
            session.close()

    def test_batch_ack_does_not_replace_backpack_confirmation(self):
        from types import SimpleNamespace
        from src.services.warehouse_state_management import WarehouseStateManagementPlan, WarehouseStateManagementService

        class Dao:
            snapshot_id = 7
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def current_inventory_snapshot_id(self): return self.snapshot_id
            def list_inventory_items(self, snapshot_id):
                return [{"uid_slot": 8, "uid_serial": 1, "locked": False, "discarded": snapshot_id == 9}]
        dao = Dao()
        class ConfirmingSync(Sync):
            is_running = True
            state = SimpleNamespace(phase="listening")
            def __init__(self):
                super().__init__()
                self.core_hello_result["capabilities"].append("equipment")
                self.waits = []
            def begin_full_inventory_guard(self, *args, **kwargs): return object()
            def end_full_inventory_guard(self, token): pass
            def wait_for_snapshot(self, *, after_snapshot_id, timeout):
                assert not self.active  # Batch lease must be released before snapshot reads.
                self.waits.append(after_snapshot_id)
                dao.snapshot_id = after_snapshot_id + 1
                return SimpleNamespace(last_snapshot_id=dao.snapshot_id)
        sync = ConfirmingSync()
        plan = WarehouseStateManagementPlan(snapshot_id=7, changes=(
            {"equipment": {"slot": 8, "serial": 1}, "target_state": "discarded", "current_state": "normal"},
        ), filter_summary={})
        result = WarehouseStateManagementService("unused.sqlite3", sync, dao_factory=lambda _: dao).apply(plan)
        self.assertEqual([7, 8], sync.waits)
        self.assertEqual(1, len(sync.calls))
        self.assertTrue(result.verified)
        self.assertEqual(9, result.after_snapshot_id)


if __name__ == "__main__":
    unittest.main()
