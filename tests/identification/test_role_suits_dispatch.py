# 验证套装配置变化只停止未派发动作，已接受部分继续核对，不连接游戏。
from concurrent.futures import CancelledError
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.integrations.inventory.warehouse_state_writer import WarehouseStateWriter
from src.integrations.nte_core_protocol import NteCoreRpcError
from src.services.identification.matching_settings import MatchingSettingsService, matching_settings_identity
from src.services.inventory.warehouse_state_management import (
    WarehouseStateManagementError, WarehouseStateManagementPlan, WarehouseStateManagementService,
)
from src.services.scanning.state_sync import GamepadStateSyncService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.inventory.test_warehouse_state_batch import Sync, changes

NTE_TEST_TIER = "core"


@pytest.mark.parametrize("batched", [False, True])
def test_change_between_dispatches_retains_only_accepted_fields(batched):
    sync = Sync(batched)
    def check_current():
        if sync.calls:
            raise CancelledError("套装配置已变化")
    result = WarehouseStateWriter(sync).apply_many(changes(17, True), check_current=check_current)
    assert len(sync.calls) == 1 and not sync.active
    assert result.stopped_reason == "套装配置已变化"
    assert len(result.accepted_operations) == (16 if batched else 1)
    assert result.group_count == (8 if batched else 0)
    if not batched:
        assert result.accepted_operations[0]["field"] == "locked"
        assert result.accepted_operations[0]["value"] is False


def test_busy_retry_rechecks_config_and_unknown_rpc_cancel_is_not_mislabeled():
    sync = Sync()
    sync.error = NteCoreRpcError({"code": -32001, "data": {"domain_code": "EQUIPMENT_PLUGIN_BUSY"}})
    def check_current():
        if sync.calls:
            raise CancelledError("套装配置已变化")
    with patch("src.integrations.inventory.warehouse_state_writer.sleep"):
        result = WarehouseStateWriter(sync).apply_many(changes(17), check_current=check_current)
    assert len(sync.calls) == 1
    assert not result.accepted_operations
    assert result.stopped_reason
    sync = Sync()
    sync.error = CancelledError("RPC outcome unknown")
    with pytest.raises(CancelledError, match="outcome unknown"):
        WarehouseStateWriter(sync).apply_many(changes(17))
    assert len(sync.calls) == 1 and not sync.active


def test_stale_plan_dispatches_nothing_and_partial_unlock_is_confirmed(tmp_path):
    database = tmp_path / "account.sqlite3"
    with UserDataDao(database, account_id="fixture"):
        pass
    settings = MatchingSettingsService(database)
    initial = settings.load()
    frozen = matching_settings_identity(initial)
    updated = {"version": 1, "extra_suits": {"1001": ["B"]}}
    plan = WarehouseStateManagementPlan(7, (
        {"equipment": {"slot": 8, "serial": 1}, "target_state": "discarded", "current_state": "locked"},
        {"equipment": {"slot": 8, "serial": 2}, "target_state": "discarded", "current_state": "locked"},
    ), {}, matching_identity=frozen)

    class Dao:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def current_inventory_snapshot_id(self): return 7
        def list_inventory_items(self, snapshot_id):
            return [{"kind": "module", "uid_slot": 8, "uid_serial": serial,
                     "locked": snapshot_id == 7 or serial == 2, "discarded": False} for serial in (1, 2)]

    class ChangingSync(Sync):
        is_running = True
        state = SimpleNamespace(phase="listening")
        def __init__(self):
            super().__init__(False)
            self.waits = []
        def set_item_locked(self, **kwargs):
            super().set_item_locked(**kwargs)
            settings.save(updated, expected=initial)
        def wait_for_snapshot(self, *, after_snapshot_id, timeout):
            self.waits.append(after_snapshot_id)
            return SimpleNamespace(last_snapshot_id=8)

    sync = ChangingSync()
    service = WarehouseStateManagementService(database, sync, dao_factory=lambda _: Dao())
    settings.save(updated, expected=initial)
    with pytest.raises(WarehouseStateManagementError, match="重新评估"):
        service.apply(plan)
    assert sync.calls == sync.waits == []
    settings.save(initial, expected=updated)
    result = service.apply(plan)
    assert len(sync.calls) == 1 and sync.waits == [7]
    assert len(result.changes) == 1 and result.changes[0]["target_state"] == "normal"
    assert result.after_snapshot_id == 8 and not result.verified
    assert "已提交部分已确认" in result.verification_error
    assert not sync.active


def test_scan_ready_callback_cannot_dispatch_a_now_stale_plan():
    called = []
    current = [True]
    scanner = SimpleNamespace(sync_equipment_states=lambda *_a, **_k: called.append("dispatch"))
    def check_current():
        if not current[0]:
            raise CancelledError("配置已变化")
    service = GamepadStateSyncService(scanner, total_drives=1,
        post_action_ready_callback=lambda: current.__setitem__(0, False),
        sleep_fn=lambda _: None, check_current=check_current)
    with pytest.raises(CancelledError, match="配置已变化"):
        service.sync([{"index": 1}], {})
    assert not called
