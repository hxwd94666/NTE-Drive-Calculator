# 用真实 Qt 线程和合成传输验证更新的代次、物理关闭、末端取消与一次派发。
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QLineEdit, QMainWindow, QPushButton

from auto_sync_ui_fixture import application
from src.app.context import AccountContext, AppContext, CallbackAccountLifecycle
from src.features.settings.updates.controller import CalcUpdateController
from src.features.settings.updates import controller as ui
from src.integrations.updates import mirror
from src.ui.app import MainWindow


def wait_until(predicate, timeout=3):
    end = monotonic() + timeout
    while not predicate() and monotonic() < end:
        application().processEvents()
        sleep(.005)
    assert predicate(), "合成更新线程未按预期退场"


def account(root, identity):
    return AccountContext(identity, identity, root / identity, root / identity / "unused.sqlite3",
                          root / identity / "config", root / identity / "shots", root / identity / "logs")


class Settings:
    def __init__(self, path, **kwargs):
        self.values = {"mirror_cdk": path.parent.name, "never_remind": False}
        self.saved = []

    def load(self, _name):
        return dict(self.values)

    def save(self, _name, values):
        self.saved.append(dict(values))
        self.values = dict(values)
        return dict(values)


class Response(BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


@pytest.fixture
def owner(tmp_path, monkeypatch):
    application()
    window = QMainWindow()
    context = AppContext(SimpleNamespace(), account(tmp_path, "first"), settings_factory=Settings)
    closes = []
    control = CalcUpdateController(identity_provider=lambda: (context.account.active_account_id, context.generation),
        load_settings=lambda: context.account_settings.load("update"),
        save_settings=lambda values: context.account_settings.save("update", values),
        style_sheet=lambda: "", show_group=lambda: None, show_netdisk=lambda _links: None,
        request_close=lambda: closes.append(True), parent=window)
    view = QLabel(), QPushButton(), QPushButton(), QLineEdit()
    control.bind_view(status=view[0], check_button=view[1], download_button=view[2], cdk_editor=view[3])
    context.register_account_lifecycle(CallbackAccountLifecycle(is_running=lambda: False,
        stop=control.stop_for_account, rebuild=lambda _account: control.rebuild_account(), start=lambda: None))
    monkeypatch.setattr(mirror.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(ui.dialogs, "_show_mirror_project_download_dialog", lambda *_args: None)
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *_args: None)
    yield SimpleNamespace(control=control, context=context, closes=closes, view=view, window=window)
    control.begin_close()
    wait_until(lambda: not control.is_running())
    window.close()
    window.deleteLater()
    for widget in view:
        widget.deleteLater()
    application().processEvents()


@pytest.mark.parametrize("transition", ["account", "aba", "close"])
def test_revoke_inflight_check_keeps_physical_owner_and_discards_result(owner, monkeypatch, tmp_path, transition):
    entered, release = Event(), Event()
    class HeldResponse(Response):
        def read1(self, size):
            entered.set()
            assert release.wait(2)
            return super().read1(size)
    monkeypatch.setattr(mirror, "_open", lambda *_args: HeldResponse(
        b'{"code":0,"data":{"version_name":"99.0.0"}}'))
    prompts = []
    monkeypatch.setattr(ui.dialogs, "show_update_dialog", lambda *_args, **_kwargs: prompts.append(True))
    owner.control.check()
    assert entered.wait(1)
    if transition == "close":
        owner.control.begin_close()
    else:
        owner.context.switch_account(account(tmp_path, "second"))
        if transition == "aba":
            owner.context.switch_account(account(tmp_path, "first"))
    assert owner.control.is_running()
    release.set()
    wait_until(lambda: not owner.control.is_running())
    assert prompts == [] and owner.closes == []
    assert owner.control._check is None


def test_rebuild_uses_published_settings_before_account_changed_notification(owner, tmp_path):
    old_settings = owner.context.account_settings
    observed = []
    owner.context.subscribe_account_changed(lambda event: observed.append(owner.view[3].text()))
    owner.context.switch_account(account(tmp_path, "second"))
    assert observed == ["second"] and owner.context.account_settings is not old_settings
    owner.view[3].setText("second-edited")
    owner.control.save_cdk()
    assert old_settings.saved == []
    assert owner.context.account_settings.saved[-1]["mirror_cdk"] == "second-edited"


def prepare_download(owner, monkeypatch):
    def response(request, _timeout):
        if "/latest?" in request.full_url:
            return Response(json.dumps({"code": 0, "data": {"version_name": "99.0.0",
                "url": "https://synthetic.invalid/setup"}}).encode())
        return Response(b"MZfixture")
    monkeypatch.setattr(mirror, "_open", response)
    owner.control.start_download()
    wait_until(lambda: owner.control._pending_launch is not None)
    owner.control._launch_timer.stop()
    return owner.control._pending_launch[1]


def test_check_failure_preserves_active_download_cancel_control(owner, monkeypatch):
    check_entered, check_release, data_entered, data_release = Event(), Event(), Event(), Event()
    class HeldResponse(Response):
        def __init__(self, body, entered, release):
            super().__init__(body)
            self.entered, self.release = entered, release
        def read1(self, size):
            self.entered.set()
            assert self.release.wait(3)
            return super().read1(size)
    def opening(request, _timeout):
        if "/latest?" in request.full_url:
            if "cdk=" not in request.full_url:
                return HeldResponse(b'{"code":1}', check_entered, check_release)
            return Response(json.dumps({"code": 0, "data": {"version_name": "99.0.0",
                "url": "https://synthetic.invalid/setup"}}).encode())
        return HeldResponse(b"MZfixture", data_entered, data_release)
    monkeypatch.setattr(mirror, "_open", opening)
    calls = []
    monkeypatch.setattr(mirror.subprocess, "Popen", lambda *_args, **_kwargs: calls.append(True))
    try:
        owner.control.check()
        wait_until(check_entered.is_set)
        assert owner.view[2].isEnabled()
        owner.control.start_download()
        wait_until(lambda: data_entered.is_set() and owner.control._progress_dialog is not None)
        progress = owner.control._progress_dialog
        check_release.set()
        wait_until(lambda: owner.control._check is None)
        # 两个请求可合法并行；检查失败不得撤掉下载所属的唯一取消入口。
        assert owner.control._progress_dialog is progress and progress.isVisible()
        assert not owner.control._download.cancelled.is_set()
        cancel = next(button for button in progress.findChildren(QPushButton) if button.text() == "取消")
        cancel.click()
        assert owner.control._download.cancelled.is_set() and owner.control.is_running()
        data_release.set()
        wait_until(lambda: not owner.control.is_running())
        assert not calls and not owner.closes
    finally:
        owner.control.cancel_download()
        check_release.set()
        data_release.set()
        wait_until(lambda: not owner.control.is_running())


@pytest.mark.parametrize("transition", ["account", "cancel", "replace", "success"])
def test_full_download_pending_dispatch_rechecks_authority_and_bytes(owner, monkeypatch, tmp_path, transition):
    receipt = prepare_download(owner, monkeypatch)
    calls = []
    monkeypatch.setattr(mirror.subprocess, "Popen", lambda *args, **kwargs: calls.append(args))
    if transition == "account":
        owner.context.switch_account(account(tmp_path, "second"))
    elif transition == "cancel":
        owner.control.cancel_download()
    elif transition == "replace":
        replacement = receipt.path.with_suffix(".tmp")
        replacement.write_bytes(b"MZreplacement")
        replacement.replace(receipt.path)
    owner.control._dispatch()
    wait_until(lambda: not owner.control.is_running())
    owner.control._dispatch()  # 已消费/失效的 timer 迟到一次也没有二次派发。
    assert len(calls) == (1 if transition == "success" else 0)
    assert len(owner.closes) == (1 if transition == "success" else 0)
    if transition == "replace":
        assert receipt.path.read_bytes() == b"MZreplacement"
    elif transition != "success":
        assert not receipt.path.exists()


def test_final_digest_is_background_cancellable_and_keeps_event_loop_live(owner, monkeypatch):
    receipt = prepare_download(owner, monkeypatch)
    entered, release = Event(), Event()
    original_open = Path.open
    class BlockedRead:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            self.stream.close()
        def fileno(self):
            return self.stream.fileno()
        def read(self, size):
            entered.set()
            assert release.wait(2)
            return self.stream.read(size)
    def opening(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return BlockedRead(stream) if path == receipt.path and args == ("rb",) else stream
    monkeypatch.setattr(Path, "open", opening)
    calls, heartbeat = [], []
    monkeypatch.setattr(mirror.subprocess, "Popen", lambda *args, **kwargs: calls.append(args))
    owner.control._dispatch()
    assert entered.wait(1)
    def cancel_from_ui():
        heartbeat.append(True)
        owner.control.cancel_download()
    QTimer.singleShot(0, owner.control, cancel_from_ui)
    wait_until(lambda: bool(heartbeat))
    assert owner.control.is_running()
    release.set()
    wait_until(lambda: not owner.control.is_running())
    assert not calls and not owner.closes and not receipt.path.exists()


def test_update_preferences_from_stale_dialog_do_not_write_new_account(owner, monkeypatch, tmp_path):
    settings = owner.context.account_settings
    def show(*_args, **_kwargs):
        owner.context.switch_account(account(tmp_path, "second"))
        return {"changed": True, "never_remind": True, "ignored_version": "99.0.0"}
    monkeypatch.setattr(ui.dialogs, "show_update_dialog", show)
    monkeypatch.setattr(mirror, "_open", lambda *_args: Response(
        b'{"code":0,"data":{"version_name":"99.0.0"}}'))
    owner.control.check()
    wait_until(lambda: not owner.control.is_running())
    assert settings.saved == [] and owner.context.account_settings.saved == []


def test_actual_mainwindow_close_waits_for_owned_update_thread(owner, monkeypatch):
    class CloseHost(MainWindow):
        def __init__(self):
            QMainWindow.__init__(self)
    host = CloseHost()
    noop = lambda *_args: None
    idle = SimpleNamespace(close=noop, stop=noop, begin_close=noop, is_running=lambda: False)
    host.equipment_persistence_controller = SimpleNamespace(defer_transition=lambda *_args, **_kwargs: False, close=noop)
    host.basic_weight_page = SimpleNamespace(task_owner=None, is_dirty=lambda: False, close_tasks=noop)
    for name in ("scanning_controller", "workshop_refresh_controller", "native_plugin_update_controller",
                 "_ui_latency_timer", "dashboard_controller", "identification_controller", "role_suit_matching",
                 "performance_controller", "character_profile_sync_controller", "auto_sync_controller",
                 "work_mode_controller", "battle_report_controller", "_mod_plugin_loading_service",
                 "static_catalog_page", "global_hotkey_manager"):
        setattr(host, name, idle)
    host.toolbox_page = SimpleNamespace(shutdown=noop)
    for name in ("_stop_inventory_sync", "_unregister_inventory_sync_lifecycle", "_unregister_character_profile_sync",
                 "_account_context_unsubscribe", "_unregister_page_tasks", "_unregister_toolbox_lifecycle",
                 "_unregister_warehouse_reads", "_unregister_equipment_persistence", "_unregister_calc_updates"):
        setattr(host, name, noop)
    host._log_enabled, host._qt_log_sink_id = False, None
    host._application_log_context = ui.OperationContext.create("application")
    host.calc_update_controller = owner.control
    owner.control.setParent(host)
    entered, release = Event(), Event()
    class HeldResponse(Response):
        def read1(self, size):
            entered.set()
            assert release.wait(2)
            return super().read1(size)
    monkeypatch.setattr(mirror, "_open", lambda *_args: HeldResponse(b"{}"))
    owner.control.check()
    assert entered.wait(1)
    assert host.close() is False and owner.control.is_running()
    release.set()
    wait_until(lambda: not owner.control.is_running())
    assert host.close() is True
    owner.control.setParent(owner.window)
    host.deleteLater()
