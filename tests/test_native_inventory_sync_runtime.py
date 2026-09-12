# 验证 DLL 库存来源复用同步线程、稳定保存与权限撤销边界。
import threading

import pytest

from src.services.inventory_sync_service import InventorySyncService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_inventory_sync_service import FakeCoreClient, item, snapshot


class NativeLease(FakeCoreClient):
    native_capture = True

    def __init__(self, *, ready=True, fail=False, during_status=None):
        super().__init__()
        self.ready, self.fail = ready, fail
        self.during_status = during_status
        self.polled = threading.Event()
        self.threads = []
        self.sent = False

    def status(self):
        self.threads.append(threading.current_thread().name)
        if self.during_status:
            self.during_status()
        if self.ready and not self.sent and not self.fail:
            self.emit(snapshot(item(1)))
            self.sent = True
        self.polled.set()
        return {
            "capture_status": "failed" if self.fail else "running",
            "capture_error_code": "NATIVE_SNAPSHOT_UNAVAILABLE" if self.fail else None,
            "native_snapshot_ready": self.ready,
            "message": "等待完整的 DLL 背包快照",
        }

    def confirm_inventory_snapshot(self, metadata):
        return self.ready and not self.fail


def native_service(tmp_path, lease, **kwargs):
    return InventorySyncService(
        tmp_path / "test.sqlite3", account_id="test", capture_source="native",
        client_factory=lambda: lease, poll_seconds=0.005, settle_seconds=0.03,
        **kwargs,
    )


def test_native_source_requires_explicit_shared_lease(tmp_path):
    with pytest.raises(ValueError, match="共享原生会话"):
        InventorySyncService(tmp_path / "unused.sqlite3", capture_source="native")
    assert not (tmp_path / "unused.sqlite3").exists()


def test_native_full_event_uses_existing_stabilizer_and_persistence(tmp_path):
    lease, checks = NativeLease(), []
    service = native_service(tmp_path, lease, operation_guard=checks.append,
                             raw_capture_enabled=True, capture_device_id="unused",
                             raw_capture_directory=tmp_path / "must-not-exist")
    try:
        service.start()
        state = service.wait_for_snapshot(timeout=3)
        assert state.capture_source == "native" and state.source_snapshot_ready
        with UserDataDao(service.database_path) as dao:
            assert dao.inventory_snapshot_summary(state.last_snapshot_id)["stored_item_count"] == 1
        assert set(checks) == {"native_sync"}
        assert lease.capture_params["device_name"] is None
        assert lease.capture_params["raw_capture"] == "disabled"
        assert "wait_for_game" not in lease.capture_params
        assert lease.threads == ["inventory-sync-service"]
        assert not (tmp_path / "must-not-exist").exists()
    finally:
        service.stop()
    assert lease.closed and lease.capture_stopped


def test_partial_native_observation_waits_without_saving(tmp_path):
    lease = NativeLease(ready=False)
    service = native_service(tmp_path, lease, operation_guard=lambda _cap: None)
    published = []
    service.add_state_handler(published.append)
    try:
        service.start()
        assert lease.polled.wait(3)
    finally:
        service.stop()
    assert any(s.message == "等待完整的 DLL 背包快照" and s.phase == "waiting" for s in published)
    assert all(s.last_snapshot_id is None and not s.source_snapshot_ready for s in published)
    assert all(s.phase != "error" for s in published)


def test_native_hard_status_error_preserves_reason(tmp_path):
    service = native_service(tmp_path, NativeLease(fail=True), operation_guard=lambda _cap: None)
    try:
        service.start()
        state = service.wait_for_phase("error", timeout=3)
        assert state.error_code == "NATIVE_SNAPSHOT_UNAVAILABLE"
        assert state.last_snapshot_id is None
    finally:
        service.stop()


@pytest.mark.parametrize("change", ["permission", "context", "stop"])
def test_native_poll_cannot_persist_after_late_revocation(tmp_path, change):
    valid = [True]
    service = None
    def revoke():
        if change == "stop": service.request_stop()
        else: valid[0] = False
    def guard(capability):
        assert capability == "native_sync"
        if change == "permission" and not valid[0]: raise PermissionError("revoked")
    lease = NativeLease(during_status=revoke)
    service = native_service(tmp_path, lease, operation_guard=guard,
                             context_is_current=lambda: valid[0] if change == "context" else True)
    try:
        service.start()
        assert lease.polled.wait(3)
    finally:
        service.stop()
    assert service.state.last_snapshot_id is None
    with UserDataDao(service.database_path) as dao:
        assert dao.current_inventory_snapshot_id() is None
