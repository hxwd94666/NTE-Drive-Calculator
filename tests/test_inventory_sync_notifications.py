# 验证库存同步 Qt 排队通知的账号、代次、服务及运行身份隔离。
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QWidget

from src.services.inventory_sync_service import InventorySyncState
from src.ui.controllers import inventory_sync_controller as module


class NotificationWindow(QWidget, module.InventorySyncControllerMixin):
    inventory_sync_state_signal = Signal(object)


class FakeSync:
    def __init__(self, *_args, **kwargs):
        self.kwargs = kwargs
        self.capture_source = kwargs.get("capture_source", "packet")
        self.handlers = []
        self.is_running = False

    def add_state_handler(self, callback):
        self.handlers.append(callback)

    def remove_state_handler(self, callback):
        if callback in self.handlers:
            self.handlers.remove(callback)

    def start(self):
        self.is_running = True

    def stop(self):
        self.is_running = False

    def emit(self):
        state = InventorySyncState(phase="listening", running=True, last_snapshot_id=7)
        for callback in tuple(self.handlers):
            callback(state)


@pytest.fixture
def notification_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(module, "InventorySyncService", FakeSync)
    window = NotificationWindow()
    window._inventory_sync_service = None
    window.app_context = SimpleNamespace(
        generation=1,
        account=SimpleNamespace(
            active_account_id="first", active_account_name="test",
            log_dir=tmp_path, user_database_path=tmp_path / "unused.sqlite3",
        ),
        paths=SimpleNamespace(app_dir=Path(tmp_path), static_database_path=tmp_path / "static.sqlite3"),
    )
    window.work_mode_service = SimpleNamespace(settings=SimpleNamespace(paused=False), allowed=lambda capability, **kwargs: capability != "native_sync")
    window.operation_guard = lambda _cap: None
    window._get_sync_settings = lambda: {}
    accepted = []
    window._on_warehouse_sync_state = accepted.append
    window.inventory_sync_state_signal.connect(window._on_inventory_sync_state, Qt.QueuedConnection)
    window._start_inventory_sync()
    yield app, window, accepted
    window._stop_inventory_sync()
    window.close()


def test_current_frozen_notification_reaches_warehouse(notification_window):
    app, window, accepted = notification_window
    window._inventory_sync_service.emit()
    assert accepted == []
    app.processEvents()
    assert len(accepted) == 1 and accepted[0].last_snapshot_id == 7


@pytest.mark.parametrize("invalidate", ["account", "generation", "service", "run"])
def test_queued_notification_drops_each_stale_identity(notification_window, invalidate):
    app, window, accepted = notification_window
    window._inventory_sync_service.emit()
    if invalidate == "account":
        window.app_context.account.active_account_id = "second"
    elif invalidate == "generation":
        window.app_context.generation += 1
    elif invalidate == "service":
        window._inventory_sync_service = FakeSync()
    else:
        window.invalidate_inventory_sync_notifications()
    app.processEvents()
    assert accepted == []


def test_late_callback_copied_before_unsubscribe_is_still_rejected(notification_window):
    app, window, accepted = notification_window
    service = window._inventory_sync_service
    late = service.handlers[0]
    window.invalidate_inventory_sync_notifications()
    assert window._inventory_sync_service is service
    late(InventorySyncState(phase="listening", running=True, last_snapshot_id=9))
    app.processEvents()
    assert accepted == []


def test_same_account_new_run_accepts_only_new_service(notification_window):
    app, window, accepted = notification_window
    original = window._inventory_sync_service
    original.emit()
    original.is_running = False
    window._start_inventory_sync()
    current = window._inventory_sync_service
    assert current is not original and original.handlers == []
    current.emit()
    app.processEvents()
    assert len(accepted) == 1


def test_unscoped_raw_state_is_rejected(notification_window):
    app, window, accepted = notification_window
    window.inventory_sync_state_signal.emit(InventorySyncState(phase="listening", running=True))
    app.processEvents()
    assert accepted == []


def test_callback_account_identity_was_frozen_at_start(notification_window):
    app, window, accepted = notification_window
    window.app_context.account.active_account_id = "second"
    window._inventory_sync_service.emit()
    app.processEvents()
    assert accepted == []


@pytest.mark.parametrize("missing_guard", [False, True])
def test_raw_directory_requires_diagnostics_before_mkdir_or_open(notification_window, monkeypatch, tmp_path, missing_guard):
    _app, window, _accepted = notification_window
    blocked_root = tmp_path / "must-not-exist"
    window.app_context.account.log_dir = blocked_root
    requested, opened, warnings = [], [], []

    def deny(capability):
        requested.append(capability)
        raise PermissionError("diagnostics denied")

    if missing_guard:
        del window.operation_guard
    else:
        window.operation_guard = deny
    monkeypatch.setattr(module.QDesktopServices, "openUrl", lambda url: opened.append(url))
    monkeypatch.setattr(module.QMessageBox, "warning", lambda *args: warnings.append(args))
    window._open_raw_capture_directory()
    assert not blocked_root.exists()
    assert opened == [] and len(warnings) == 1
    assert requested == ([] if missing_guard else ["diagnostics"])


@pytest.mark.parametrize("automatic", [False, True])
def test_disallowed_sync_guides_only_manual_entry(notification_window, automatic):
    _app, window, _accepted = notification_window
    window._stop_inventory_sync()
    prompts = []
    window.work_mode_service.allowed = lambda *args, **kwargs: False
    window.operation_entry = lambda *args: prompts.append(args) or False
    window._start_inventory_sync(automatic=automatic)
    assert prompts == ([] if automatic else [("game_sync", "背包同步")])
    assert window._inventory_sync_service is None


@pytest.mark.parametrize("automatic", [False, True])
def test_paused_sync_has_detection_guidance_only_for_manual_entry(notification_window, automatic):
    _app, window, _accepted = notification_window
    window._stop_inventory_sync()
    prompts = []
    window.work_mode_service.settings.paused = True
    window.operation_unavailable = lambda *args, **kwargs: prompts.append((args, kwargs))
    window._start_inventory_sync(automatic=automatic)
    assert len(prompts) == (0 if automatic else 1)
    if prompts: assert prompts[0][1] == {"target": "detection"}
    assert window._inventory_sync_service is None


@pytest.mark.parametrize("automatic", [False, True])
def test_sync_start_missing_core_guides_only_manual_entry(notification_window, monkeypatch, automatic):
    from src.integrations.nte_core_protocol import NteCoreNotFoundError
    _app, window, _accepted = notification_window
    window._stop_inventory_sync()
    prompts = []
    window.operation_unavailable = lambda *args, **kwargs: prompts.append((args, kwargs))
    def missing(*args, **kwargs): raise NteCoreNotFoundError("missing Core")
    monkeypatch.setattr(module, "InventorySyncService", missing)
    window._start_inventory_sync(automatic=automatic)
    assert len(prompts) == (0 if automatic else 1)


@pytest.mark.parametrize("automatic", [False, True])
def test_async_sync_connection_fault_is_manual_once_only(notification_window, automatic):
    app, window, _accepted = notification_window
    window._stop_inventory_sync()
    prompts = []
    window.operation_unavailable = lambda *args, **kwargs: prompts.append((args, kwargs))
    window._start_inventory_sync(automatic=automatic)
    callback = window._inventory_sync_service.handlers[0]
    error = InventorySyncState(phase="error", running=False, error="missing", error_code="NPCAP_NOT_FOUND")
    callback(error)
    callback(error)
    app.processEvents()
    assert len(prompts) == (0 if automatic else 1)


def test_new_raw_setting_denial_does_not_save_local_draft(notification_window):
    _app, window, _accepted = notification_window
    window._stop_inventory_sync()
    saves, prompts = [], []
    window._account_settings = SimpleNamespace(load=lambda _key: {"raw_capture_enabled": False}, save=lambda *args: saves.append(args))
    window._sync_raw_capture_toggle = SimpleNamespace(isChecked=lambda: True)
    window.operation_entry = lambda *args: prompts.append(args) or False
    assert window._save_capture_diagnostics() is None
    assert saves == [] and prompts == [("diagnostics", "原始抓包诊断")]


def test_existing_raw_setting_does_not_block_unrelated_local_save(notification_window, monkeypatch):
    _app, window, _accepted = notification_window
    window._stop_inventory_sync()
    saves, prompts = [], []
    window._account_settings = SimpleNamespace(load=lambda _key: {"raw_capture_enabled": True}, save=lambda *args: saves.append(args) or {})
    window._sync_raw_capture_toggle = SimpleNamespace(isChecked=lambda: True)
    window._sync_capture_device_edit = SimpleNamespace(text=lambda: "")
    window.operation_entry = lambda *args: prompts.append(args) or False
    monkeypatch.setattr(module.QMessageBox, "information", lambda *args: None)
    window._save_capture_diagnostics()
    assert len(saves) == 1 and prompts == []



@pytest.mark.parametrize("automatic", [False, True])
def test_native_sync_mode_uses_shared_lease_without_packet_fallback(notification_window, automatic):
    _app, window, _accepted = notification_window
    window._stop_inventory_sync()
    entries, unavailable = [], []
    window.work_mode_service.allowed = lambda capability, **kwargs: capability == "native_sync"
    lease = object()
    window.native_game_session = SimpleNamespace(inventory_client=lambda: lease)
    window.operation_entry = lambda *args: entries.append(args) or False
    window.operation_unavailable = lambda *args, **kwargs: unavailable.append((args, kwargs))
    window._start_inventory_sync(automatic=automatic)
    service = window._inventory_sync_service
    assert entries == unavailable == []
    assert service.kwargs["capture_source"] == "native"
    assert service.kwargs["client_factory"]() is lease
    assert service.kwargs["raw_capture_enabled"] is False


@pytest.mark.parametrize("change", ["mode", "pause", "generation", "connected"])
def test_async_sync_guidance_drops_revoked_stale_or_already_connected_run(notification_window, change):
    app, window, _accepted = notification_window
    prompts = []
    window.operation_unavailable = lambda *args, **kwargs: prompts.append((args, kwargs))
    callback = window._inventory_sync_service.handlers[0]
    if change == "mode": window.work_mode_service.allowed = lambda *args, **kwargs: False
    elif change == "pause": window.work_mode_service.settings.paused = True
    elif change == "generation": window.app_context.generation += 1
    else:
        callback(InventorySyncState(phase="waiting", running=True, capturing=True))
    callback(InventorySyncState(phase="error", error="missing", error_code="NPCAP_NOT_FOUND"))
    app.processEvents()
    assert prompts == []



def test_automatic_sync_requires_automation_permission_even_when_manual_is_allowed(notification_window):
    _app, window, _accepted = notification_window
    window._stop_inventory_sync()
    prompts = []
    window.work_mode_service.allowed = lambda capability, **kwargs: not kwargs.get("automatic", False)
    window.operation_entry = lambda *args: prompts.append(args) or False
    window.operation_unavailable = lambda *args, **kwargs: prompts.append(args)
    window._start_inventory_sync(automatic=True)
    assert window._inventory_sync_service is None and prompts == []


def test_raw_directory_disallowed_mode_uses_guidance_without_filesystem_effect(notification_window, tmp_path):
    _app, window, _accepted = notification_window
    target = tmp_path / "not-created"
    window.app_context.account.log_dir = target
    window.work_mode_service.allowed = lambda *args, **kwargs: False
    prompts = []
    window.operation_entry = lambda *args: prompts.append(args) or False
    window._open_raw_capture_directory()
    assert prompts == [("diagnostics", "诊断抓包")]
    assert not target.exists()


@pytest.mark.parametrize("change", ["account", "generation", "service", "run"])
def test_guidance_return_rechecks_identity_before_warehouse_update(notification_window, change):
    app, window, accepted = notification_window
    def navigate(*args, **kwargs):
        if change == "account": window.app_context.account.active_account_id = "other"
        elif change == "generation": window.app_context.generation += 1
        elif change == "service": window._inventory_sync_service = FakeSync()
        else: window.invalidate_inventory_sync_notifications()
    window.operation_unavailable = navigate
    window._inventory_sync_service.handlers[0](InventorySyncState(
        phase="error", error="missing", error_code="NPCAP_NOT_FOUND",
    ))
    app.processEvents()
    assert accepted == []


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("code", ["NATIVE_MAPPING_UNSUPPORTED", "NATIVE_CAPABILITY_MISSING"])
def test_native_missing_contract_guides_manual_to_component_settings(notification_window, automatic, code):
    app, window, _accepted = notification_window
    window._stop_inventory_sync()
    window.work_mode_service.allowed = lambda cap, **kwargs: cap == "native_sync"
    window.native_game_session = SimpleNamespace(inventory_client=lambda: object())
    prompts = []
    window.operation_unavailable = lambda *args, **kwargs: prompts.append((args, kwargs))
    window._start_inventory_sync(automatic=automatic)
    callback = window._inventory_sync_service.handlers[0]
    callback(InventorySyncState(phase="error", error="当前组件缺少正式库存能力", error_code=code, capture_source="native"))
    app.processEvents()
    assert len(prompts) == (0 if automatic else 1)
    if prompts:
        assert prompts[0][1] == {"target": "deployment"}


def test_automatic_permission_is_rechecked_by_frozen_service_guard(notification_window):
    _app, window, _accepted = notification_window
    window._stop_inventory_sync()
    window._start_inventory_sync(automatic=True)
    guard = window._inventory_sync_service.kwargs["operation_guard"]
    guard("packet_capture")
    window.work_mode_service.allowed = lambda _cap, **kwargs: not kwargs.get("automatic")
    with pytest.raises(PermissionError):
        guard("packet_capture")


@pytest.mark.parametrize("source", ["native", "packet"])
def test_sync_error_source_reaches_both_manual_guidance_and_home(notification_window, source):
    from PySide6.QtWidgets import QLabel
    app, window, _accepted = notification_window
    window._stop_inventory_sync()
    window.work_mode_service.allowed = lambda cap, **kwargs: cap == (
        "native_sync" if source == "native" else "packet_capture"
    )
    window.native_game_session = SimpleNamespace(inventory_client=lambda: object())
    prompts = []
    window.operation_unavailable = lambda *args, **kwargs: prompts.append(args)
    window.auto_sync_controller = SimpleNamespace(inventory_state_changed=lambda _state: None, render=lambda: None)
    window.home_sync_badge = QLabel(window)
    window.home_sync_detail = QLabel(window)
    window._start_inventory_sync()
    window._inventory_sync_service.handlers[0](InventorySyncState(
        phase="error", error="游戏未运行", error_code="GAME_PROCESS_NOT_FOUND", capture_source=source,
    ))
    app.processEvents()
    assert len(prompts) == 1
    for text in (prompts[0][1], window.home_sync_detail.text()):
        if source == "native":
            assert "完全退出游戏" in text
            assert "登录页" not in text
        else:
            assert "待抓包监听就绪后再登录" in text
