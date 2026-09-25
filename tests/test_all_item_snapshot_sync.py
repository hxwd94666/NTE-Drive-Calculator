# 验证全部物品沿共享同步会话延后归档，取消或写入失败不破坏装备快照。
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.domain.all_item_snapshot import ALL_ITEMS_CAPABILITY
from src.integrations.native_inventory_snapshot import NativeSnapshotPending
from src.observability import OperationContext
from src.services.all_item_snapshot_storage import store_all_item_snapshot
from src.services.inventory_capture_wait import InventorySyncCancelled
from src.services.native_game_session import NativeGameSession
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError
from tests.test_native_all_item_snapshot import AllItemSource
from tests.test_native_change_sync import ChangeCore


class MixedCore(ChangeCore):
    def __init__(self):
        super().__init__()
        self.hello_result["capabilities"].append(ALL_ITEMS_CAPABILITY)
        self.all_items = AllItemSource()
        self.all_items.raw.update(providerId="fixture", domainKey=self.header["domainKey"], revision="1")
        self.all_items.revision = "1"
        self.fail_all = False

    def call(self, method, params, **kwargs):
        if params.get("scope") == "all_items":
            if self.fail_all:
                raise NativeSnapshotPending("synthetic pending")
            return self.all_items.call(method, params)
        return super().call(method, params, **kwargs)


def test_equipment_is_emitted_first_then_all_items_wait_for_commit_ack(tmp_path):
    core = MixedCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    events = []
    lease.add_event_handler("event.inventory.snapshot", events.append)
    lease.start_capture(profile="inventory")
    service = SimpleNamespace(account_id="a", _stop_requested=Event(), _context_is_current=lambda: True,
                              capture_source="native", _operation_guard=lambda _cap: None,
                              _operation_context=OperationContext.create("all_item_test"))
    try:
        assert "native_all_item_snapshot" not in lease.status()
        assert len(events) == 1 and len(events[0]["params"]["items"]) == 1
        assert core.all_items.calls == []
        status = lease.status()
        assert len(status["native_all_item_snapshot"]["records"]) == 65
        assert len(events) == 1
        with UserDataDao(tmp_path / "a.sqlite3", account_id="a") as dao:
            with patch.object(dao, "save_all_item_snapshot", side_effect=UserDataError("synthetic failure")):
                store_all_item_snapshot(service, dao, lease, status)
            assert lease.status()["native_all_item_snapshot"] is status["native_all_item_snapshot"]
            store_all_item_snapshot(service, dao, lease, status)
            assert dao.latest_all_item_snapshot() == core.all_items.raw
            assert dao.current_inventory_snapshot_id() is None
        assert "native_all_item_snapshot" not in lease.status()
        assert sum(method == "native.snapshot.refresh" for method, _ in core.all_items.calls) == 1
        assert len(events) == 1
    finally:
        lease.close(); session.close()


def test_pending_all_items_does_not_revoke_equipment_ready():
    core = MixedCore(); core.fail_all = True
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client(); lease.start_capture(profile="inventory")
    try:
        assert lease.status()["native_snapshot_ready"]
        status = lease.status()
        assert status["native_snapshot_ready"]
        assert status["native_all_item_error"] == "NativeSnapshotPending"
    finally:
        lease.close(); session.close()


def test_account_generation_revocation_never_stores_or_acknowledges(tmp_path):
    source = AllItemSource()
    service = SimpleNamespace(account_id="a", _stop_requested=Event(), _context_is_current=lambda: False,
                              capture_source="native", _operation_guard=lambda _cap: None,
                              _operation_context=OperationContext.create("all_item_test"))
    with UserDataDao(tmp_path / "a.sqlite3", account_id="a") as dao:
        with pytest.raises(InventorySyncCancelled):
            store_all_item_snapshot(service, dao, None, {"native_all_item_snapshot": source.raw})
        assert dao.latest_all_item_snapshot() is None
