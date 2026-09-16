# 验证正常等待、硬故障、取消与迟到写入授权边界。
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.inventory_capture_wait import (
    CaptureStartError, CaptureWaitMonitor, InventorySyncCancelled,
    require_inventory_operation, wait_capture_ready,
)
from src.services.inventory_sync_service import InventorySyncService


def waiting_service():
    return SimpleNamespace(
        capture_source="packet",
        _operation_guard=lambda _capability: None,
        _context_is_current=None,
        _stop_requested=threading.Event(),
        _capture_ready=threading.Event(),
        _capture_monitor=CaptureWaitMonitor(),
        _poll_seconds=0.001,
        _publish=lambda *args, **kwargs: None,
    )


def test_wait_capability_outlives_old_deadline_then_runs():
    service = waiting_service()
    statuses = iter([
        {"capture_status": "waiting_game", "capture_operation_id": "current"},
        {"capture_status": "waiting_network", "capture_operation_id": "current"},
        {"capture_status": "running", "capture_operation_id": "current"},
    ])
    client = SimpleNamespace(status=lambda: next(statuses))
    with patch("src.services.inventory_capture_wait.time.monotonic", side_effect=[0, 20, 40, 60]):
        assert wait_capture_ready(service, client, supports_wait=True)


def test_old_core_retains_bounded_initialization_timeout():
    service = waiting_service()
    with patch("src.services.inventory_capture_wait.time.monotonic", side_effect=[0, 20]):
        with pytest.raises(TimeoutError):
            wait_capture_ready(service, object(), supports_wait=False)


def test_wait_error_preserves_machine_code():
    service = waiting_service()
    client = SimpleNamespace(status=lambda: {
        "capture_status": "failed", "capture_error_code": "NPCAP_NOT_FOUND",
    })
    with pytest.raises(CaptureStartError) as raised:
        wait_capture_ready(service, client, supports_wait=True)
    assert raised.value.domain_code == "NPCAP_NOT_FOUND"


def test_wait_stop_cancels_without_running():
    service = waiting_service()
    service._stop_requested.set()
    assert not wait_capture_ready(service, object(), supports_wait=True)


def test_stale_operation_cannot_mark_new_capture_ready():
    monitor = CaptureWaitMonitor()
    monitor.update({"capture_status": "waiting_game", "capture_operation_id": "new"})
    monitor.update({"status": "running", "operation_id": "old"})
    assert monitor.read()[0] == "waiting_game"


def test_start_requires_explicit_execution_guard(tmp_path):
    service = InventorySyncService(tmp_path / "unused.sqlite3")
    with pytest.raises(PermissionError):
        service.start()
    assert not service.is_running
    assert not service.database_path.exists()


def test_context_invalidated_before_write_cancels():
    service = waiting_service()
    service._context_is_current = lambda: False
    with pytest.raises(InventorySyncCancelled):
        require_inventory_operation(service)


def test_revoked_source_drops_late_inventory_callback(tmp_path):
    def deny(_capability):
        raise PermissionError("revoked")

    service = InventorySyncService(tmp_path / "unused.sqlite3", operation_guard=deny)
    service._on_inventory_event({"params": {"complete": True, "items": []}})
    assert service._take_latest_event() is None


class WaitingCore:
    hello_result = {"protocol_version": 1, "capabilities": ["inventory", "capture_wait_v1"]}

    def __init__(self):
        self.capture_params = None
        self.stopped = False
        self.closed = False

    def start(self):
        return self

    def add_event_handler(self, *_args):
        pass

    def remove_event_handler(self, *_args):
        pass

    def start_capture(self, **kwargs):
        self.capture_params = kwargs
        return {"capture_status": "waiting_game", "capture_operation_id": "new"}

    def status(self):
        return {"capture_status": "waiting_game", "capture_operation_id": "new"}

    def stop_capture(self):
        self.stopped = True
        return {}

    def close(self):
        self.closed = True


def test_runtime_negotiates_wait_and_stop_cancels_core(tmp_path):
    core = WaitingCore()
    service = InventorySyncService(
        tmp_path / "account.sqlite3", account_id="test", client_factory=lambda: core,
        operation_guard=lambda _capability: None, poll_seconds=0.001,
    )
    try:
        service.start()
        state = service.wait_for_phase("waiting", timeout=2)
        assert state.running and not state.capturing
        assert core.capture_params["wait_for_game"] is True
        assert state.last_snapshot_id is None
    finally:
        service.stop()
    assert core.stopped and core.closed


def test_raw_diagnostics_require_separate_capability(tmp_path):
    core = WaitingCore()

    def guard(capability):
        if capability == "diagnostics":
            raise PermissionError("diagnostics denied")

    service = InventorySyncService(
        tmp_path / "account.sqlite3", account_id="test", client_factory=lambda: core,
        operation_guard=guard, raw_capture_enabled=True, poll_seconds=0.001,
    )
    try:
        service.start()
        state = service.wait_for_phase("error", timeout=2)
        assert not state.running
        assert "diagnostics denied" in state.error
        assert core.capture_params is None
    finally:
        service.stop()
