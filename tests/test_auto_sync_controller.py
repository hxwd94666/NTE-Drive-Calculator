# 验证首页自动同步的游戏等待、重试、取消与旧会话隔离。
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QWidget

from auto_sync_ui_fixture import application, dispose

from src.domain.work_mode import WorkModeProbe, NativeFeatureProbe
from src.services.work_mode_service import WorkModeService
from src.ui.controllers.auto_sync_controller import AutoSyncController


class Watcher:
    def __init__(self, on_change):
        self.on_change = on_change
        self.is_running = False

    def start(self):
        self.is_running = True

    def stop(self):
        self.is_running = False


class Inventory:
    def __init__(self):
        self.is_running = True
        self.requested = False
        self.state = SimpleNamespace(phase="waiting", capturing=False, source_snapshot_ready=False, message="正在准备背包监听")

    def request_stop(self):
        self.requested = True

    def stop(self):
        self.is_running = False


@pytest.fixture
def owner(tmp_path):
    app = application()
    window = QWidget()
    window.app_context = SimpleNamespace(generation=1, account=SimpleNamespace(active_account_id="test"))
    window._inventory_sync_service = None
    window.battle_report_controller = SimpleNamespace(is_running=lambda: False)
    window.work_mode_controller = SimpleNamespace(is_transitioning=False, refresh_controls=lambda: None)
    window.invalidate_inventory_sync_notifications = lambda: None
    window.operation_entry = lambda *_: False
    window.operation_unavailable = lambda *_args, **_kw: None
    starts, jobs, watchers = [], [], []

    def start(**kwargs):
        starts.append(kwargs)
        window._inventory_sync_service = Inventory()

    window._start_inventory_sync = start
    window._stop_inventory_sync = lambda: setattr(window, "_inventory_sync_service", None)
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("low", risk_confirmed=True)
    policy.enable_auto_sync_after_preflight()
    policy.set_cleanup_pending(False)

    def watcher_factory(on_change):
        watcher = Watcher(on_change)
        watchers.append(watcher)
        return watcher

    c = AutoSyncController(window=window, policy=policy, request_check=lambda: None,
                           watcher_factory=watcher_factory, submit_stop=jobs.append)
    c.start()
    yield c, window, policy, starts, jobs, watchers, app
    c.close()
    for job in jobs:
        job()
    app.processEvents()
    dispose(window)


def found(watcher, pid=12):
    watcher.on_change(SimpleNamespace(kind="started", process=(pid, 1), error=""))


def test_no_core_until_game_and_no_duplicate_start(owner):
    c, window, _policy, starts, _jobs, watchers, _app = owner
    assert not starts and len(watchers) == 1
    found(watchers[-1])
    c.refresh()
    c.refresh()
    assert len(starts) == 1
    assert window._inventory_sync_service.is_running


def test_game_exit_waits_for_old_owner_before_relaunch(owner):
    c, window, _policy, starts, jobs, watchers, _app = owner
    found(watchers[-1])
    old = window._inventory_sync_service
    watchers[-1].on_change(SimpleNamespace(kind="exited", process=(12, 1), error=""))
    assert old.requested and len(jobs) == 1
    found(watchers[-1], pid=13)
    assert len(starts) == 1
    jobs.pop(0)()
    c.refresh()
    assert len(starts) == 2 and window._inventory_sync_service is not old


def test_toggle_off_only_stops_inventory_not_manual_battle(owner):
    c, window, policy, starts, jobs, watchers, _app = owner
    found(watchers[-1])
    window.battle_report_controller.is_running = lambda: True
    c.set_enabled(False)
    assert not policy.settings.auto_sync_enabled
    assert not policy.settings.paused
    assert window._inventory_sync_service.requested
    jobs.pop(0)()
    c.refresh()
    assert len(starts) == 1


def test_first_enable_waits_for_visible_preflight_confirmation(owner):
    c, _window, policy, _starts, _jobs, _watchers, _app = owner
    c.set_enabled(False)
    pending = []
    c._request_enable_preflight = pending.append
    c.set_enabled(True)
    assert len(pending) == 1
    assert not policy.settings.auto_sync_enabled
    assert pending[0]() is True
    assert policy.settings.auto_sync_enabled
    assert policy.settings.component_auto_ready


def test_confirmed_preflight_can_resume_existing_cleanup_pause(owner):
    c, _window, policy, _starts, _jobs, _watchers, _app = owner
    c.set_enabled(False)
    policy.set_paused(True)
    pending = []
    c._request_enable_preflight = pending.append
    c.set_enabled(True)
    assert len(pending) == 1
    assert policy.settings.paused and not policy.settings.auto_sync_enabled
    assert pending[0]() is True
    assert not policy.settings.paused and policy.settings.auto_sync_enabled


def test_existing_enabled_preference_still_rechecks_before_resuming_pause(owner):
    c, _window, policy, _starts, _jobs, _watchers, _app = owner
    policy.set_paused(True)
    pending = []
    c._request_enable_preflight = pending.append
    c.set_enabled(True)
    assert len(pending) == 1
    assert policy.settings.auto_sync_enabled and policy.settings.paused
    assert pending[0]() is True
    assert policy.settings.auto_sync_enabled and not policy.settings.paused


def test_paused_workbench_shows_effective_off_state_and_resume_action(owner):
    from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton

    c, window, policy, _starts, _jobs, _watchers, _app = owner
    window.home_auto_sync_toggle = QCheckBox(window)
    window.home_restart_sync_button = QPushButton(window)
    for name in ('home_sync_source_label', 'home_sync_action_hint', 'home_sync_detail', 'home_sync_badge'):
        setattr(window, name, QLabel(window))
    policy.set_paused(True)
    c.render()
    assert not window.home_auto_sync_toggle.isChecked()
    assert window.home_restart_sync_button.text() == '恢复自动同步'
    assert '无需重选工作模式' in window.home_sync_detail.text()
    pending = []
    c._request_enable_preflight = pending.append
    c.open_restart()
    assert len(pending) == 1


def test_packet_sync_can_start_while_packet_battle_runs(owner):
    c, window, _policy, starts, _jobs, watchers, _app = owner
    window.battle_report_controller.is_running = lambda: True
    found(watchers[-1])
    assert len(starts) == 1


def test_restart_drops_old_owner_before_start_and_keeps_enabled(owner):
    c, window, policy, starts, jobs, watchers, _app = owner
    found(watchers[-1])
    old = window._inventory_sync_service
    c.restart()
    c.refresh()
    assert old.requested and len(starts) == 1
    jobs.pop(0)()
    c.refresh()
    assert len(starts) == 2 and policy.settings.auto_sync_enabled


def test_failed_stop_never_starts_replacement(owner):
    c, window, _policy, starts, jobs, watchers, _app = owner
    found(watchers[-1])
    old = window._inventory_sync_service
    old.stop = lambda: (_ for _ in ()).throw(TimeoutError())
    c.restart()
    jobs.pop(0)()
    c.refresh()
    assert len(starts) == 1 and window._inventory_sync_service is old


def test_late_watcher_callback_after_account_change_is_ignored(owner):
    c, window, _policy, starts, _jobs, watchers, _app = owner
    previous = watchers[-1]
    window.app_context.generation += 1
    c.refresh()
    found(previous)
    assert not starts
    found(watchers[-1])
    assert len(starts) == 1


def test_medium_waits_for_native_readiness_and_battle_end(owner):
    c, window, policy, starts, _jobs, _watchers, _app = owner
    policy.select_mode("medium", risk_confirmed=True)
    c.refresh()
    c.observe_probe(WorkModeProbe(game_running=True, core_available=True, npcap_available=False))
    assert not starts
    window.battle_report_controller.is_running = lambda: True
    c.observe_probe(WorkModeProbe(game_running=True, core_available=True,
                                 native_inventory=NativeFeatureProbe(handshake=True)))
    assert not starts
    window.battle_report_controller.is_running = lambda: False
    c.refresh()
    assert len(starts) == 1


@pytest.mark.parametrize('mode', ['medium', 'developer'])
def test_native_retry_without_game_tracks_waiting_and_component_readiness(owner, mode):
    from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton
    c, window, policy, starts, _jobs, _watchers, _app = owner
    policy.select_mode(mode, risk_confirmed=True)
    c.refresh()
    window.home_auto_sync_toggle = QCheckBox(window)
    window.home_restart_sync_button = QPushButton(window)
    for name in ('home_sync_source_label', 'home_sync_action_hint', 'home_sync_detail', 'home_sync_badge'):
        setattr(window, name, QLabel(window))
    c.open_restart()
    view = c._dialog
    assert '完全退出游戏' in view.detail.text()
    c.observe_probe(WorkModeProbe(game_running=False))
    assert '完全退出游戏' in view.detail.text()
    view.begin.click()
    assert not starts
    assert '等待启动游戏' in view.detail.text()
    assert '收尾旧会话' not in view.detail.text()
    assert '等待启动游戏' in window.home_sync_detail.text()
    assert window.home_sync_badge.text() == '等待游戏'
    window.status_lbl = QLabel('同步异常', window)
    window._inventory_sync_service = SimpleNamespace(is_running=False,
        state=SimpleNamespace(phase='error', message='nte-core stdout closed unexpectedly'))
    c.render()
    assert window.home_sync_badge.text() == '等待游戏'
    assert window.status_lbl.text() == '等待游戏'
    c.observe_probe(WorkModeProbe(game_running=True, core_available=True))
    assert window.status_lbl.text() == '同步异常'
    window._inventory_sync_service = None
    c.observe_probe(WorkModeProbe(game_running=True, core_available=True))
    assert not starts
    assert '等待同步组件就绪' in view.detail.text()
    c.observe_probe(WorkModeProbe(game_running=True, core_available=True,
                                 native_inventory=NativeFeatureProbe(handshake=True)))
    assert len(starts) == 1
    assert '等待启动游戏' not in view.detail.text()
    assert '等待同步组件就绪' not in view.detail.text()


@pytest.mark.parametrize('mode', ['medium', 'developer'])
def test_native_retry_requires_exit_before_deployment_then_waits_for_game(owner, mode):
    from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton
    c, window, policy, starts, _jobs, _watchers, _app = owner
    policy.select_mode(mode, risk_confirmed=True)
    c.refresh()
    window.home_auto_sync_toggle = QCheckBox(window)
    window.home_restart_sync_button = QPushButton(window)
    for name in ('home_sync_source_label', 'home_sync_action_hint', 'home_sync_detail', 'home_sync_badge'):
        setattr(window, name, QLabel(window))
    c.open_restart()
    view = c._dialog
    view.begin.click()
    for running, expected in ((True, 'waiting_game_exit'), (False, 'waiting_deployment')):
        c.observe_probe(WorkModeProbe(
            game_running=running, core_available=True,
            native_load=NativeFeatureProbe(files=False),
            native_inventory=NativeFeatureProbe(handshake=True),
        ))
        assert c.preparation_state == expected
        assert not starts
        assert '登录界面' not in view.detail.text()
        if running:
            assert '完全退出游戏' in view.detail.text()
            assert '完全退出游戏' in window.home_sync_detail.text()
            assert window.home_sync_badge.text() == '等待退出游戏'
        else:
            assert '暂勿启动游戏' in view.detail.text()
            assert '暂勿启动游戏' in window.home_sync_detail.text()
    c.observe_probe(WorkModeProbe(
        game_running=False, core_available=True, native_load=NativeFeatureProbe(files=True),
    ))
    assert c.preparation_state == 'waiting_game'
    assert '等待启动游戏' in view.detail.text()
    assert not starts
    c.observe_probe(WorkModeProbe(
        game_running=True, core_available=True, native_load=NativeFeatureProbe(files=True),
        native_inventory=NativeFeatureProbe(handshake=True),
    ))
    assert len(starts) == 1


def test_cancel_restart_prevents_queued_start_without_disabling_preference(owner):
    c, _window, policy, starts, jobs, watchers, _app = owner
    found(watchers[-1])
    c.restart()
    c.cancel_restart()
    jobs.pop(0)()
    c.refresh()
    assert len(starts) == 1 and policy.settings.auto_sync_enabled


@pytest.mark.parametrize('mode', ['medium', 'developer'])
def test_close_retry_while_waiting_does_not_block_login_auto_sync(owner, mode):
    c, _window, policy, starts, _jobs, _watchers, app = owner
    policy.select_mode(mode, risk_confirmed=True)
    c.refresh()
    c.observe_probe(WorkModeProbe(game_running=False))
    c.open_restart()
    c._dialog.begin.click()
    c._dialog.close()
    app.processEvents()
    assert not c._retry_cancelled and policy.settings.auto_sync_enabled
    c.observe_probe(WorkModeProbe(game_running=True, core_available=True,
                                 native_inventory=NativeFeatureProbe(handshake=True)))
    assert len(starts) == 1


def test_close_ignores_queued_process_event(owner):
    c, _window, _policy, starts, _jobs, watchers, _app = owner
    c.close()
    found(watchers[-1])
    c.refresh()
    assert not starts


def test_path_change_replaces_watcher_and_ignores_previous_identity(owner):
    c, _window, policy, starts, _jobs, watchers, _app = owner
    previous = watchers[-1]
    policy.set_game_executable("changed-game.exe")
    c.refresh()
    found(previous)
    assert not starts and len(watchers) == 2
    found(watchers[-1])
    assert len(starts) == 1


def test_old_watcher_must_finish_closing_before_new_one_starts(owner):
    c, _window, policy, _starts, _jobs, watchers, _app = owner
    previous = watchers[-1]
    previous.worker_alive = True
    policy.set_game_executable("changed-game.exe")
    c.refresh()
    assert len(watchers) == 1
    previous.worker_alive = False
    c.refresh()
    assert len(watchers) == 2


def test_watch_failure_stops_old_inventory_without_claiming_game_exit(owner):
    c, window, _policy, starts, jobs, watchers, _app = owner
    found(watchers[-1])
    old = window._inventory_sync_service
    watchers[-1].on_change(SimpleNamespace(kind="error", process=(12, 1), error="无法监控游戏进程"))
    assert old.requested and not c.packet_game_running
    jobs.pop(0)()
    assert len(starts) == 1


def test_native_game_exit_stops_inventory_and_reconnects_after_ready(owner):
    c, window, policy, starts, jobs, _watchers, _app = owner
    policy.select_mode("medium", risk_confirmed=True)
    c.refresh()
    ready = WorkModeProbe(game_running=True, core_available=True,
                          native_inventory=NativeFeatureProbe(handshake=True))
    c.observe_probe(ready)
    old = window._inventory_sync_service
    c.observe_probe(WorkModeProbe(game_running=False))
    assert old.requested
    c.observe_probe(ready)
    assert len(starts) == 1
    jobs.pop(0)()
    assert len(starts) == 2


def test_home_only_has_auto_and_restart_and_uses_metric_for_last_saved(owner, tmp_path):
    from PySide6.QtWidgets import QPushButton
    from src.features.home.page import build_home_page, refresh_home_page
    c, window, policy, _starts, _jobs, _watchers, app = owner
    window.app_context.paths = SimpleNamespace(asset_dir=tmp_path)
    window.work_mode_service = policy
    window.auto_sync_controller = c
    window.work_mode_controller.check = lambda **_kwargs: None
    window._go = lambda _key: None
    page = build_home_page(window)
    titles = [button.text() for button in page.findChildren(QPushButton)]
    assert "重启同步" in titles and "如何使用" in titles
    assert "停止同步" not in titles and "背包同步" not in titles
    refresh_home_page(window, {
        "account": {"account_name": "测试"}, "loadout_plan_count": 2,
        "static": {"counts": {"character": 3}},
        "characters": {"catalog_count": 22, "synced_count": 21, "profile_count": 21},
        "inventory": {"stored_item_count": 8, "module_count": 6, "core_count": 2,
                      "equipped_count": 1, "snapshot_id": 4, "captured_at_utc": "2026-01-01"},
    })
    assert "上次保存" in window.home_last_sync_label.text()
    assert window.home_last_sync_label.isHidden()
    assert "快照 #4" in window.home_metric_labels["inventory"][1].text()
    assert window.home_metric_labels["characters"][0].text() == "22"
    assert "21" in window.home_metric_labels["characters"][1].text()
    assert window.home_sync_title.text() == "背包同步"
    assert window.home_character_sync_detail.isHidden()
    assert window.home_sync_source_label.isHidden()
    assert "等待启动游戏" in window.home_sync_detail.text()
    window.home_auto_sync_toggle.setChecked(False)
    app.processEvents()
    assert not policy.settings.auto_sync_enabled
    assert window.home_restart_sync_button.text() == "开启自动同步"
    assert "上次保存" in window.home_last_sync_label.text()
    assert window.home_sync_source_label.text() == "来源：抓包（角色养成需手动维护）"
    dispose(page)


@pytest.mark.parametrize("mode", ["medium", "developer"])
def test_native_home_hides_redundant_rows_but_surfaces_role_failure(owner, tmp_path, mode):
    from src.features.home.page import build_home_page, refresh_home_page
    c, window, policy, _starts, _jobs, _watchers, _app = owner
    policy.select_mode(mode, risk_confirmed=True)
    window.app_context.paths = SimpleNamespace(asset_dir=tmp_path)
    window.work_mode_service = policy
    window.auto_sync_controller = c
    window.work_mode_controller.check = lambda **_kwargs: None
    window._go = lambda _key: None
    page = build_home_page(window)
    dashboard = {"account": {"account_name": "测试"}, "inventory": None, "loadout_plan_count": 0,
                 "characters": {"catalog_count": 22, "synced_count": 21, "profile_count": 21}}
    refresh_home_page(window, dashboard)
    assert window.home_sync_title.text() == "游戏数据同步"
    assert window.home_sync_source_label.text() == "来源：游戏内组件"
    assert all(field in window.home_sync_source_label.toolTip() for field in ("等级", "突破", "技能", "好感度", "弧盘"))
    assert window.home_sync_source_label.isHidden()
    assert window.home_character_sync_detail.isHidden()
    service = Inventory()
    service.state.character_sync_error = "角色自动同步未保存，已保留原养成。"
    window._inventory_sync_service = service
    c.render()
    assert not window.home_character_sync_detail.isHidden()
    assert "未保存" in window.home_character_sync_detail.text()
    service.state.character_sync_error = None
    c.render()
    assert window.home_character_sync_detail.isHidden()
    dashboard["characters"].update(synced_count=0, profile_count=0)
    refresh_home_page(window, dashboard)
    assert "尚未同步" in window.home_metric_labels["characters"][1].text()
    assert window.home_character_sync_detail.isHidden()
    dispose(page)


def test_home_sync_help_keeps_usage_steps_and_mode_boundaries():
    from src.features.home.page import _home_sync_help_text

    low = _home_sync_help_text("low")
    native = _home_sync_help_text("medium")
    offline = _home_sync_help_text("offline")

    expected = (
        "1. 开启“自动同步”，按提示确认抓包环境。\n"
        "2. 启动游戏，等待抓包监听就绪后再登录；完整背包会自动保存。\n"
        "3. 数据未更新？点“重启同步”，按提示重新登录；仍有问题时看“检测详情”。"
    )
    assert low == expected
    assert '先退出游戏' in native
    assert '退出启动器' in native
    assert '游戏场景' in native
    assert '退回登录界面' not in native
    assert _home_sync_help_text('developer') == native
    assert '离线模式' in offline and '工作模式设置' in offline
    assert '登录界面' not in offline and '登录页' not in offline


@pytest.mark.parametrize('action, target', [
    ('工作模式设置', 'mode'), ('环境设置', 'game_path'), ('关闭', None),
])
def test_home_sync_help_routes_only_on_selected_button(monkeypatch, action, target):
    from PySide6.QtWidgets import QDialog, QLabel, QPushButton
    from src.features.home.page import _show_home_sync_help

    application()
    window = QWidget()
    window.work_mode_service = SimpleNamespace(
        settings=SimpleNamespace(mode=SimpleNamespace(value='low')),
    )
    routes = []
    window.work_mode_controller = SimpleNamespace(open_settings=routes.append)

    def interact(dialog):
        assert dialog.objectName() == 'homeSyncHelpDialog'
        assert '抓包监听' in dialog.findChild(QLabel, 'homeSyncHelpInstructions').text()
        buttons = dialog.findChildren(QPushButton)
        assert {button.text() for button in buttons} == {'工作模式设置', '环境设置', '关闭'}
        next(button for button in buttons if button.text() == action).click()
        return dialog.result()

    monkeypatch.setattr(QDialog, 'exec', interact)
    try:
        _show_home_sync_help(window)
        assert routes == ([target] if target is not None else [])
    finally:
        dispose(window)


@pytest.mark.parametrize('mode', ['medium', 'developer'])
def test_native_home_waiting_preserves_data_reason_without_login_instructions(owner, mode):
    from PySide6.QtWidgets import QCheckBox, QLabel, QPushButton
    from src.services.inventory_sync_service import InventorySyncState
    c, window, policy, _starts, _jobs, _watchers, _app = owner
    policy.select_mode(mode, risk_confirmed=True)
    c.refresh()
    c.observe_probe(WorkModeProbe(
        game_running=True, core_available=True, native_load=NativeFeatureProbe(files=True),
        native_inventory=NativeFeatureProbe(handshake=True),
    ))
    window.home_auto_sync_toggle = QCheckBox(window)
    window.home_restart_sync_button = QPushButton(window)
    for name in ('home_sync_source_label', 'home_sync_action_hint', 'home_sync_detail', 'home_sync_badge'):
        setattr(window, name, QLabel(window))
    message = '正在等待游戏提供完整的同步数据。'
    window._inventory_sync_service.state = InventorySyncState(
        phase='waiting', capturing=True, capture_source='native', message=message,
    )
    c.render()
    assert message in window.home_sync_detail.text()
    assert '游戏场景' in window.home_sync_detail.text()
    assert '等待登录背包' not in window.home_sync_detail.text()
    assert '登录界面' not in window.home_sync_detail.text()
