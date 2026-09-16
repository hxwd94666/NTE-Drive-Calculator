# 在离屏界面和测试替身中验证工作模式意图、过期结果及观察者收尾。
import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QWidget

from auto_sync_ui_fixture import application, dispose

from src.domain.work_mode import WorkModeProbe
from src.services.game_observation_service import GameObservationService, ObservationResult
from src.services.work_mode_service import WorkModeService
from src.ui.controllers import work_mode_controller as module


class QueuedObserver:
    def __init__(self, *, tick, publish):
        self.tick, self.publish = tick, publish
        self.jobs = []
        self.closed = False
        self.finalize = None

    def start(self):
        pass

    def submit(self, job, *, key=None):
        if self.closed:
            return False
        if key is not None:
            self.jobs = [(old_key, old) for old_key, old in self.jobs if old_key != key]
        self.jobs.append((key, job))
        return True

    def run_jobs(self):
        while self.jobs:
            result = self.jobs.pop(0)[1]()
            if result is not None and not self.closed:
                self.publish(result)

    def close(self, *, finalize=None):
        self.closed = True
        self.jobs.clear()
        self.finalize = finalize


@pytest.fixture(scope="module")
def qt_app():
    return application()


@pytest.fixture
def controller(tmp_path, monkeypatch, qt_app):
    monkeypatch.setattr(module, "GameObservationService", QueuedObserver)
    popups, events = [], []
    monkeypatch.setattr(module, "show_mode_report", lambda *args: popups.append("report"))
    monkeypatch.setattr(module.QMessageBox, "warning", lambda *args: popups.append("warning"))
    monkeypatch.setattr(module.QMessageBox, "information", lambda *args: popups.append("info"))
    monkeypatch.setattr(module, "confirm_mode", lambda *args: True)
    window = QWidget()
    window.app_context = SimpleNamespace(generation=1)
    window.global_hotkey_manager = SimpleNamespace(request_stop=lambda: events.append("input_stop"))
    window.battle_report_controller = SimpleNamespace(
        stop=lambda: events.append("battle_stop"), is_running=lambda: False,
        set_work_mode_presentation=lambda: None,
    )
    window.scanning_controller = SimpleNamespace(request_stop=lambda: events.append("scan_request"))
    window._mod_plugin_loading_service = SimpleNamespace(stop_loader=lambda: events.append("loader_close"))
    window._inventory_sync_service = None
    window.invalidate_inventory_sync_notifications = lambda: events.append("invalidate_run")
    window._start_inventory_sync = lambda **_kwargs: events.append("packet_start")
    window.observed_sync_probes = []
    window.auto_sync_controller = SimpleNamespace(
        observe_probe=window.observed_sync_probes.append, refresh=lambda: None,
    )
    policy = WorkModeService(tmp_path / "settings.json")
    policy.select_mode("low", risk_confirmed=True)
    policy.set_cleanup_pending(False)
    native = SimpleNamespace(
        request_close=lambda **kwargs: events.append("native_request"),
        close=lambda: events.append("native_close"),
    )
    probe = WorkModeProbe(game_running=True, npcap_available=True, core_available=True)
    runtime = SimpleNamespace(
        path_detail="", cleanup_detail="", cleanup_exit_detail="",
        native_session=native, invalidate=lambda: events.append("invalidate"),
        tick=lambda **kwargs: probe, discover=lambda: (),
        request_close=lambda: events.append("runtime_request"),
        close=lambda: events.append("runtime_close"),
    )
    value = module.WorkModeController(window=window, policy=policy, runtime=runtime)
    yield value, window, policy, events, popups, probe
    value.close()
    if value._observer.finalize:
        value._observer.finalize()
    dispose(window)


def test_close_with_unverified_path_does_not_report_cleanup_failure(controller, monkeypatch):
    c, _window, policy, _events, _popups, _probe = controller
    messages = []
    monkeypatch.setattr(module.QMessageBox, "information", lambda _owner, title, message: messages.append((title, message)))
    policy.set_cleanup_pending(True)
    c.runtime.path_detail = "尚未找到有效游戏路径。"
    c.close()
    assert messages == []
    assert policy.settings.pending_cleanup


@pytest.mark.parametrize("detail", [
    "已停止后续加载；等待游戏退出后清理，当前 DLL 尚未卸载。",
    "组件文件或加载配置归属未知或已修改，请手动核对；尚未清理。",
])
def test_close_pending_cleanup_shows_observed_reason_without_clearing_record(controller, monkeypatch, detail):
    c, _window, policy, _events, _popups, _probe = controller
    messages = []
    monkeypatch.setattr(module.QMessageBox, "information", lambda _owner, title, message: messages.append((title, message)))
    policy.update_deployment({"deployed_sha256": "old-component", "loading_method": "proxy"})
    policy.set_cleanup_pending(True)
    c.runtime.cleanup_exit_detail = detail
    c.close()
    assert messages[0][0] == "游戏组件清理提示"
    assert detail in messages[0][1] and "按上述原因处理" in messages[0][1]
    assert policy.settings.pending_cleanup
    assert policy.deployment_record["deployed_sha256"] == "old-component"


def test_close_does_not_turn_general_detection_message_into_cleanup_failure(controller):
    c, _window, policy, _events, popups, _probe = controller
    policy.set_cleanup_pending(True)
    c.runtime.cleanup_detail = "原生组件已部署；启动游戏后重新核对连接和各项能力。"
    c.close()
    assert popups == []
    assert policy.settings.pending_cleanup


def test_stale_revision_generation_and_fault_do_not_start_or_show(controller):
    c, window, policy, events, popups, probe = controller
    c._show_request_id = 4
    c._apply((policy.settings.revision - 1, 1, probe, 0))
    c._apply((policy.settings.revision, 0, probe, 4))
    c._apply(ObservationResult("fault", "stale", policy.settings.revision - 1, 1, 4))
    assert events == [] and popups == []
    assert window.observed_sync_probes == []


def test_one_manual_check_one_popup_background_cannot_consume_it(controller):
    c, _window, policy, _events, popups, probe = controller
    c.check(show=True)
    request_id = c._show_request_id
    c._apply((policy.settings.revision, 1, probe, 0))
    assert popups == []
    c._observer.run_jobs()
    c._apply((policy.settings.revision, 1, probe, request_id))
    assert popups == ["report"]


def test_coalesced_checks_do_not_duplicate_dialogs(controller):
    c, *_rest, popups, _probe = controller
    c.check(show=True)
    c.check(show=True)
    assert len(c._observer.jobs) == 1
    c._observer.run_jobs()
    assert popups == ["report"]


def test_manual_failure_popup_is_consumed_once(controller):
    c, _window, policy, _events, popups, _probe = controller
    c.check(show=True)
    request_id = c._show_request_id
    result = ObservationResult("fault", "failed", policy.settings.revision, 1, request_id)
    c._apply(result)
    c._apply(result)
    assert popups == ["warning"]


def test_paused_manual_check_does_not_connect(controller):
    c, _window, policy, _events, _popups, probe = controller
    policy.set_paused(True)
    observed = []
    c.runtime.tick = lambda **kwargs: observed.append(kwargs) or probe
    c.check(show=True)
    c._observer.run_jobs()
    assert observed == [{"allow_connect": False}]


def test_offline_revokes_capture_and_queues_teardown_before_next_observation(controller):
    c, window, policy, events, _popups, probe = controller
    c.select_mode("offline")
    assert policy.settings.mode.value == "offline"
    assert not policy.allowed("packet_capture", automatic=True)
    assert events[:3] == ["input_stop", "battle_stop", "scan_request"]
    assert "native_close" not in events and "loader_close" not in events
    c._observer.run_jobs()
    c._apply((policy.settings.revision, 1, probe, 0))
    assert "packet_start" not in events
    assert window.observed_sync_probes
    assert events.index("battle_stop") < events.index("loader_close") < events.index("native_close")


def test_explicit_cleanup_disables_auto_redeployment(controller):
    c, _window, policy, _events, _popups, _probe = controller
    c.cleanup()
    assert policy.settings.paused
    assert not policy.allowed("native_load", automatic=True)
    assert policy.settings.pending_cleanup


def test_mode_change_does_not_take_runtime_lock_on_ui_thread(controller):
    c, _window, policy, events, _popups, _probe = controller
    c.select_mode("offline")
    assert policy.settings.mode.value == "offline"
    assert "invalidate" not in events and "native_close" not in events
    c._observer.run_jobs()
    assert "native_close" in events and "invalidate" in events


def test_close_only_requests_shutdown_and_drops_late_result(controller):
    c, window, policy, events, popups, probe = controller
    c.check(show=True)
    c.close()
    assert "runtime_request" in events
    assert "runtime_close" not in events
    c._apply((policy.settings.revision, 1, probe, 1))
    c.check(show=True)
    c.start()
    assert not c._observer.jobs and "packet_start" not in events
    assert window.observed_sync_probes == []
    assert popups == []
    c._observer.finalize()
    assert "runtime_close" in events


def test_previous_persisted_snapshot_is_not_current_login_proof(controller):
    c, window, policy, _events, _popups, probe = controller
    window._inventory_sync_service = SimpleNamespace(
        is_running=True,
        state=SimpleNamespace(capturing=True, source_snapshot_ready=False, last_snapshot_id=99, error=None),
    )
    captured = []
    original = policy.build_report
    policy.build_report = lambda value: captured.append(value) or original(value)
    c._apply((policy.settings.revision, 1, probe, 0))
    assert not captured[0].packet_snapshot and not captured[0].logged_in
    assert window.observed_sync_probes == captured
    window._inventory_sync_service = None


def test_observer_close_is_nonblocking_and_never_publishes_late_tick():
    entered, release = threading.Event(), threading.Event()
    published, finalized = [], []

    def tick():
        entered.set()
        release.wait(2)
        return "late"

    observer = GameObservationService(tick=tick, publish=published.append, interval=0.001)
    observer.start()
    assert entered.wait(1)
    started = time.monotonic()
    observer.close(finalize=lambda: finalized.append("done"))
    assert time.monotonic() - started < 0.2
    release.set()
    assert observer.wait_closed(1)
    observer._thread.join(1)
    observer.start()
    assert not observer._thread.is_alive()
    assert published == [] and finalized == ["done"]


def test_observer_closed_before_start_cannot_create_new_work():
    called = []
    observer = GameObservationService(tick=lambda: called.append("tick"), publish=called.append)
    observer.close()
    observer.start()
    assert not observer.submit(lambda: called.append("job"))
    assert observer.wait_closed(0.1)
    assert called == []


@pytest.mark.parametrize("action", ["mode", "cleanup"])
def test_persistence_failure_still_requests_stop_after_memory_revocation(controller, monkeypatch, action):
    c, _window, policy, events, popups, _probe = controller
    policy.select_mode("medium", risk_confirmed=True)
    policy.set_cleanup_pending(False)

    def fail_save(_settings):
        raise OSError("disk full")

    monkeypatch.setattr(policy._store, "save", fail_save)
    actions = {
        "mode": lambda: c.select_mode("offline"),
        "cleanup": c.cleanup,
    }
    actions[action]()
    assert events[:3] == ["input_stop", "battle_stop", "scan_request"]
    assert "native_request" in events
    assert "native_close" not in events
    assert c._teardown_pending == 1
    assert popups == ["warning"]
    if action == "mode":
        assert policy.settings.mode.value == "offline"
        assert not policy.allowed("native_sync")
    if action == "cleanup":
        assert policy.settings.pending_cleanup and policy.settings.paused
    c._observer.run_jobs()
    assert "native_close" in events


def test_async_mode_stop_invalidates_run_without_clearing_live_service(controller):
    c, window, _policy, events, _popups, _probe = controller
    service = SimpleNamespace(is_running=True)
    service.request_stop = lambda: events.append("packet_request")

    def stop():
        service.is_running = False

    service.stop = stop
    window._inventory_sync_service = service
    cleared = []

    def clear_owner():
        cleared.append(window._inventory_sync_service)
        window._inventory_sync_service = None

    window._stop_inventory_sync = clear_owner
    c.select_mode("offline")
    assert window._inventory_sync_service is service and service.is_running
    assert "invalidate_run" in events
    c._observer.run_jobs()
    assert cleared == [service] and window._inventory_sync_service is None


def test_old_teardown_does_not_clear_replacement_service(controller):
    c, window, policy, _events, _popups, _probe = controller
    old = SimpleNamespace(is_running=False)
    current = SimpleNamespace(is_running=True)
    window._inventory_sync_service = current
    c._apply(module._TeardownResult(policy.settings.revision, 1, service=old))
    assert window._inventory_sync_service is current
    window._inventory_sync_service = None


def test_previous_pending_teardown_does_not_skip_new_failed_revocation(controller, monkeypatch):
    c, _window, policy, events, popups, _probe = controller
    policy.select_mode("medium", risk_confirmed=True)
    c._teardown_pending = 1

    def fail_save(_settings):
        raise OSError("disk full")

    monkeypatch.setattr(policy._store, "save", fail_save)
    c.select_mode("offline")
    assert events[:3] == ["input_stop", "battle_stop", "scan_request"]
    assert "native_request" in events
    assert c._teardown_pending == 2
    assert policy.settings.mode.value == "offline"
    assert popups == ["warning"]


@pytest.mark.parametrize("target", ["low", "medium", "developer"])
def test_cancel_then_reselect_survives_background_and_can_confirm(controller, monkeypatch, target):
    from PySide6.QtWidgets import QVBoxLayout
    from src.features.settings.work_mode_card import build_work_mode_card
    c, window, policy, events, _popups, probe = controller
    policy.select_mode("offline")
    window.work_mode_controller, window.work_mode_service = c, policy

    def make_card(_title):
        card = QWidget(window)
        QVBoxLayout(card)
        return card
    window._card = make_card
    card = build_work_mode_card(window)
    combo, *_rest = c._controls
    combo.setCurrentIndex(combo.findData(target))
    policy.set_cleanup_pending(False)  # An unrelated persisted revision must not replace the draft.
    for _ in range(2):
        c._apply((policy.settings.revision, 1, probe, 0))
    assert combo.currentData() == target
    monkeypatch.setattr(module, "confirm_mode", lambda *args: False)
    c.select_mode(target)
    assert policy.settings.mode.value == "offline"
    assert combo.currentData() == "offline"
    assert events == []
    combo.setCurrentIndex(combo.findData(target))
    c._apply((policy.settings.revision, 1, probe, 0))
    assert combo.currentData() == target
    monkeypatch.setattr(module, "confirm_mode", lambda *args: True)
    c.select_mode(combo.currentData())
    assert policy.settings.mode.value == target
    assert combo.currentData() == target
    assert policy.allowed("compare_sources") == (target == "developer")
    reopened = WorkModeService(policy._store.path)
    assert reopened.settings.mode.value == target and reopened.settings.risk_confirmed
    card.close()


@pytest.mark.parametrize("target", ["mode", "detection", "deployment"])
def test_guidance_navigation_uses_key_focuses_anchor_without_work(controller, qt_app, target):
    from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout
    c, window, policy, events, _popups, _probe = controller
    window.resize(680, 400)
    outer = QVBoxLayout(window)
    stack = QStackedWidget(window)
    outer.addWidget(stack)
    other = QWidget()
    stack.addWidget(other)
    content = QWidget()
    body = QVBoxLayout(content)
    mode_card = QWidget()
    modes = QVBoxLayout(mode_card)
    combo = QComboBox()
    for value in ("offline", "low", "medium", "developer"):
        combo.addItem(value, value)
    status, check = QLabel("当前检测结果"), QPushButton("检测详情")
    modes.addWidget(combo)
    modes.addWidget(check)
    modes.addWidget(status)
    body.addWidget(mode_card)
    body.addSpacing(900)
    component_card = QWidget()
    component_layout = QVBoxLayout(component_card)
    deploy = QPushButton("显式部署")
    component_layout.addWidget(deploy)
    body.addWidget(component_card)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    stack.addWidget(scroll)
    c.attach_controls(combo, status, check)
    c.attach_settings_targets(scroll=scroll, mode_card=mode_card, component_card=component_card, component_focus=deploy)
    combo.setCurrentIndex(combo.findData("developer"))
    routes = []

    def navigate(key):
        routes.append(key)
        stack.setCurrentWidget(scroll)
    c._navigate = navigate
    frozen, jobs = policy.settings, list(c._observer.jobs)
    window.show()
    qt_app.processEvents()
    c.open_settings(target)
    qt_app.processEvents()
    card, focus = c._settings_targets[target]
    assert routes == ["settings"]
    assert focus.hasFocus() and card.property("workModeHighlight") is True
    assert combo.currentData() == "developer"
    assert policy.settings == frozen and c._observer.jobs == jobs and events == []
    if target == "deployment":
        assert scroll.verticalScrollBar().value() > 0
    c._highlight_timer.timeout.emit()
    assert card.property("workModeHighlight") is False


def test_guidance_respects_cancelled_page_navigation(controller, qt_app):
    from PySide6.QtWidgets import QScrollArea
    c, window, policy, events, _popups, _probe = controller
    card = QWidget(window)
    card.hide()
    c._settings_scroll = QScrollArea()
    c._settings_targets = {"mode": (card, card)}
    c._navigate = lambda _key: None  # Existing page's unsaved-edit prompt prevented navigation.
    c.open_settings()
    qt_app.processEvents()
    assert not card.property("workModeHighlight") and not card.hasFocus()
    assert events == [] and not c._observer.jobs


def test_medium_probe_without_npcap_is_forwarded_without_starting_core(controller):
    from dataclasses import replace
    from src.domain.work_mode import NativeFeatureProbe
    c, window, policy, events, popups, probe = controller
    policy.select_mode("medium", risk_confirmed=True)
    policy.set_cleanup_pending(False)
    probe = replace(probe, game_running=True, core_available=True, npcap_available=False,
                    native_inventory=NativeFeatureProbe(handshake=True))
    c._apply((policy.settings.revision, 1, probe, 0))
    assert window.observed_sync_probes == [probe]
    assert "packet_start" not in events
    assert popups == []
    c._apply((policy.settings.revision, 1, probe, 0))
    assert window.observed_sync_probes == [probe, probe]
    assert "packet_start" not in events


def test_native_saved_inventory_does_not_mark_packet_capture_ready(controller):
    c, window, policy, _events, _popups, probe = controller
    window._inventory_sync_service = SimpleNamespace(
        is_running=True, state=SimpleNamespace(capture_source="native", capturing=True,
                                              source_snapshot_ready=True, error=None),
    )
    seen = []
    original = policy.build_report
    policy.build_report = lambda value: seen.append(value) or original(value)
    c._apply((policy.settings.revision, 1, probe, 0))
    assert not seen[0].packet_snapshot and not seen[0].packet_listening
    assert window.observed_sync_probes == seen
    window._inventory_sync_service = None


def test_native_connection_gaps_are_forwarded_to_automatic_sync_owner(controller):
    from dataclasses import replace
    from src.domain.work_mode import NativeFeatureProbe
    c, window, policy, events, popups, probe = controller
    policy.select_mode("medium", risk_confirmed=True)
    ready = replace(probe, native_inventory=NativeFeatureProbe(handshake=True))
    c._apply((policy.settings.revision, 1, ready, 0))
    c._apply((policy.settings.revision, 1, ready, 0))
    unavailable = replace(ready, game_running=False, native_inventory=NativeFeatureProbe(handshake=False))
    c._apply((policy.settings.revision, 1, unavailable, 0))
    c._apply((policy.settings.revision, 1, ready, 0))
    assert window.observed_sync_probes == [ready, ready, unavailable, ready]
    policy.set_paused(True)
    c._apply((policy.settings.revision, 1, unavailable, 0))
    c._apply((policy.settings.revision, 1, ready, 0))
    assert window.observed_sync_probes == [ready, ready, unavailable, ready, unavailable, ready]
    assert "packet_start" not in events and popups == []


def test_settings_card_keeps_only_compact_status_and_explicit_details(controller):
    from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QPushButton, QVBoxLayout
    from src.features.settings.work_mode_card import build_work_mode_card, report_text
    c, window, policy, _events, popups, probe = controller
    policy.select_mode("developer", risk_confirmed=True)
    window.work_mode_controller, window.work_mode_service = c, policy

    def make_card(_title):
        card = QWidget(window)
        QVBoxLayout(card)
        return card
    window._card = make_card
    card = build_work_mode_card(window)
    assert len(card.findChildren(QComboBox)) == 1
    assert card.findChildren(QCheckBox) == []
    assert all("开发采集来源" not in label.text() for label in card.findChildren(QLabel))
    _combo, status, check = c._controls
    c._apply((policy.settings.revision, 1, probe, 0))
    assert "\n" not in status.text()
    assert status.text() != report_text(policy.build_report(probe))
    assert popups == [] and check.text() == "检测详情"
    check.click()
    request = c._show_request_id
    c._apply((policy.settings.revision, 1, probe, request))
    assert popups == ["report"]
    policy.set_paused(True)
    c.refresh_controls()
    assert all(button.text() not in {"暂停自动管理", "继续自动管理", "暂停", "继续"}
               for button in card.findChildren(QPushButton))
    card.close()
