# 验证更新结果先登记再重连，账号切换与登记失败不会恢复错误会话。
import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QWidget, QMessageBox

from auto_sync_ui_fixture import application, dispose
from src.features.settings.native_plugin_update_controller import NativePluginUpdateController
from src.integrations.components.native_files import NativePluginUpdateResult


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    application()
    parent = QWidget()
    events = []
    policy = Mock()
    policy.deployment_record = {'workspace_path': str(tmp_path), 'loading_method': 'native-capture'}
    policy.update_deployment.side_effect = lambda _: events.append('save')
    maintenance = Mock(uncertain=False)
    maintenance.finish.side_effect = lambda **kw: events.append(('finish', kw['restore']))
    generation = [1]
    controller = NativePluginUpdateController(context=Mock(), policy=policy, session=Mock(),
        loader=Mock(), generation=lambda: generation[0], maintenance=maintenance, parent=parent)
    controller._target_directory = tmp_path
    controller._request_generation = 1
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    monkeypatch.setattr(QMessageBox, 'information', Mock())
    yield SimpleNamespace(controller=controller, policy=policy, maintenance=maintenance,
                          generation=generation, events=events)
    dispose(parent)


def test_records_verified_images_before_resuming(scenario):
    s = scenario
    result = NativePluginUpdateResult('updated', 'done', {'plugins/NTE_PluginCombat.dll': 'hash'})
    s.controller._result(result)
    assert s.events == ['save', ('finish', True)]
    s.controller._result(result)
    assert s.events == ['save', ('finish', True)]


def test_completed_deployment_is_recorded_even_if_account_changed(scenario):
    s = scenario
    s.generation[0] = 2
    s.controller._result(NativePluginUpdateResult('updated', 'done', {'plugin': 'hash'}))
    assert s.events == ['save', ('finish', False)]
    QMessageBox.information.assert_not_called()


def test_record_failure_keeps_maintenance_hold(scenario):
    s = scenario
    s.policy.update_deployment.side_effect = OSError('fixture')
    s.controller._result(NativePluginUpdateResult('updated', 'done', {'plugin': 'hash'}))
    assert s.maintenance.uncertain
    QMessageBox.warning.assert_called_once()
    QMessageBox.information.assert_not_called()


def test_loader_update_records_native_workspace_identity(scenario, tmp_path):
    s = scenario
    s.policy.deployment_record = {'loading_method': 'loader', 'native_workspace_root': str(tmp_path),
                                  'workspace_path': str(tmp_path / 'legacy')}
    s.controller._result(NativePluginUpdateResult('updated', 'done', {'plugin': 'hash'}))
    record = s.policy.update_deployment.call_args.args[0]
    assert record['native_workspace_files'] == {'plugin': 'hash'}
    assert not s.maintenance.uncertain


def test_retargeted_directory_does_not_receive_previous_game_hashes(scenario, tmp_path):
    s = scenario
    s.policy.deployment_record = {'workspace_path': str(tmp_path / 'other')}
    s.controller._result(NativePluginUpdateResult('updated', 'done', {'plugin': 'hash'}))
    s.policy.update_deployment.assert_not_called()
    assert s.maintenance.uncertain


def test_missing_game_explains_deployment_without_claiming_migration(scenario, monkeypatch):
    from src.features.settings import native_plugin_update_controller as module
    monkeypatch.setattr(module, 'native_game_pid', lambda: None)
    scenario.controller.request()
    detail = QMessageBox.warning.call_args.args[2]
    assert '游戏尚未启动' in detail
    assert '部署原生组件' in detail
    assert '首次迁移' not in detail
    scenario.maintenance.begin.assert_not_called()


def test_game_target_does_not_require_capture_pipe(monkeypatch):
    from src.integrations import native_capture_process as module
    monkeypatch.setattr(module, '_native_game_identity', lambda: (123, 456))
    monkeypatch.setattr(module, 'native_capture_game_pid', Mock(side_effect=AssertionError('capture pipe not required')))
    assert module.native_game_pid() == 123


def test_game_identity_error_is_not_reported_as_missing_game(scenario, monkeypatch):
    from src.features.settings import native_plugin_update_controller as module
    monkeypatch.setattr(module, 'native_game_pid', Mock(side_effect=RuntimeError('无法核对游戏进程身份')))
    scenario.controller.request()
    detail = QMessageBox.warning.call_args.args[2]
    assert '无法核对游戏进程身份' in detail
    assert '游戏尚未启动' not in detail

# 复用已验收的正式窗口夹具，只将维护执行边界替换为合成阻塞任务。
from dataclasses import replace
from threading import Event, Timer
from PySide6.QtCore import QTimer
from tests.allocation import test_physical_retirement as _retirement
from src.integrations.native_host_control import NativeHostOutcomeUnknown
from src.services.components.maintenance import NativePluginMaintenance
from src.storage.sqlite.user_data_dao import UserDataDao

retirement_desktop = _retirement.desktop


@pytest.fixture
def retiring_update(retirement_desktop, tmp_path, monkeypatch, request):
    qt, window, _allocation = retirement_desktop
    controller = window.native_plugin_update_controller
    directory = tmp_path / 'synthetic-component-directory'
    policy = Mock()
    policy.settings = SimpleNamespace(game_executable=str(directory / 'HTGame.exe'))
    policy.deployment_record = {'workspace_path': str(directory), 'loading_method': 'native-capture'}
    lease, session, sync, performance, reconnect = Mock(), Mock(), Mock(), Mock(), Mock()
    session.reserve_plugin_maintenance.return_value = lease
    performance.wait_plugin_update_idle.return_value = True
    maintenance = NativePluginMaintenance(session=session, sync=sync, performance=performance,
        check_blockers=lambda: None, reconnect=reconnect)
    entered, release = Event(), Event()
    calls = []
    outcome = getattr(request, 'param', 'known')
    def accepted_update(**_kwargs):
        calls.append('accepted')
        entered.set()
        assert release.wait(5), '合成已接受任务未收到释放信号'
        if outcome == 'unknown':
            raise NativeHostOutcomeUnknown('synthetic outcome unknown')
        return NativePluginUpdateResult('updated', 'synthetic done', {'plugin': 'synthetic-hash'})
    original_run = maintenance.run
    monkeypatch.setattr(maintenance, 'run', lambda **kw: original_run(**kw, update=accepted_update))
    monkeypatch.setattr(controller, 'policy', policy)
    monkeypatch.setattr(controller, 'maintenance', maintenance)
    monkeypatch.setattr('src.services.components.maintenance.log_event', lambda *_a, **_k: None)
    monkeypatch.setattr('src.features.settings.native_plugin_update_controller.native_game_pid', lambda: 123)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    monkeypatch.setattr(QMessageBox, 'information', Mock())
    controller.request()
    _retirement.spin_until(entered.is_set)
    worker = controller.worker
    # 原始阻塞行为也能有界收尾；测试失败不强退已接受的 QThread。
    fallback = Timer(1, release.set)
    fallback.daemon = True
    state = SimpleNamespace(qt=qt, window=window, controller=controller, worker=worker,
        entered=entered, release=release, fallback=fallback, calls=calls, policy=policy, maintenance=maintenance,
        lease=lease, sync=sync, performance=performance, reconnect=reconnect)
    try:
        yield state
    finally:
        release.set()
        fallback.cancel()
        if fallback.ident is not None:
            fallback.join()
        _retirement.spin_until(lambda: controller.worker is None)


def test_pending_update_stop_returns_with_worker_held_and_gui_alive(retiring_update):
    s = retiring_update
    s.fallback.start()
    s.controller.stop()
    assert not s.release.is_set(), 'stop 在 GUI 线程等待了合成外部任务'
    assert s.controller.worker is s.worker and s.worker.isRunning()
    assert s.worker.isInterruptionRequested()
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    s.qt.processEvents()
    assert heartbeat and not s.release.is_set()
    s.controller.request()
    assert s.calls == ['accepted']
    s.release.set()
    _retirement.spin_until(lambda: s.controller.worker is None)
    s.policy.update_deployment.assert_called_once()
    s.lease.close.assert_called_once()
    s.sync.resume_after_plugin_update.assert_called_once_with(restore=False)
    s.reconnect.assert_not_called()
    QMessageBox.information.assert_not_called()
    QMessageBox.warning.assert_not_called()


def test_window_close_holds_pending_update_until_physical_exit(retiring_update):
    s = retiring_update
    s.fallback.start()
    s.window.close()  # 正式 MainWindow.closeEvent 与现有 25ms 物理门禁。
    assert not s.release.is_set(), '窗口关闭同步等待了维护任务'
    assert s.window.isVisible() and s.worker.isRunning()
    s.qt.processEvents()
    assert s.window.isVisible() and s.controller.worker is s.worker
    s.controller.request()
    assert s.calls == ['accepted']
    s.release.set()
    _retirement.spin_until(lambda: not s.window.isVisible())
    assert s.controller.worker is None
    s.controller.request()  # 关闭后的控制器不接受新的更新。
    assert s.calls == ['accepted']
    s.policy.update_deployment.assert_called_once()
    s.reconnect.assert_not_called()
    QMessageBox.information.assert_not_called()
    QMessageBox.warning.assert_not_called()


def test_account_switch_retains_generation_until_update_physically_retires(retiring_update, tmp_path):
    s = retiring_update
    context = s.window.app_context
    previous, generation = context.account, context.generation
    root = tmp_path / 'second-account'
    target = replace(previous, active_account_id='synthetic-second', active_account_name='synthetic-second',
        account_data_root=root, user_database_path=root / 'user_data.sqlite3', user_config_dir=root / 'config',
        screenshot_dir=root / 'screenshots', log_dir=root / 'logs')
    with UserDataDao(target.user_database_path, account_id=target.active_account_id,
                     account_name=target.active_account_name):
        pass
    s.fallback.start()
    with pytest.raises(RuntimeError, match='插件更新正在收尾'):
        context.switch_account(target)
    assert context.account is previous and context.generation == generation
    assert not s.release.is_set() and s.worker.isRunning()
    s.release.set()
    _retirement.spin_until(lambda: s.controller.worker is None)
    context.switch_account(target)
    assert context.account is target and context.generation == generation + 1
    s.policy.update_deployment.assert_called_once()
    s.reconnect.assert_not_called()
    QMessageBox.information.assert_not_called()
    QMessageBox.warning.assert_not_called()


@pytest.mark.parametrize('retiring_update', ['unknown'], indirect=True)
def test_unknown_update_releases_thread_not_maintenance_hold_or_resend(retiring_update):
    s = retiring_update
    s.fallback.start()
    s.controller.stop()
    s.release.set()
    _retirement.spin_until(lambda: s.controller.worker is None)
    assert s.maintenance.uncertain and s.controller.running
    s.controller.request()
    assert s.calls == ['accepted']
    s.policy.update_deployment.assert_not_called()
    s.lease.close.assert_not_called()
    s.sync.resume_after_plugin_update.assert_not_called()
    s.reconnect.assert_not_called()
    QMessageBox.information.assert_not_called()
    QMessageBox.warning.assert_not_called()


def test_window_account_precheck_preserves_index_and_owners_then_retries(retiring_update, monkeypatch):
    s = retiring_update
    context, manager = s.window.app_context, s.window._account_manager
    previous, generation = context.account, context.generation
    index = manager.read_index()
    index['accounts'].append({'id': 'synthetic-target', 'name': 'synthetic-target'})
    manager.write_index(index)
    original_index = manager.accounts_index_file.read_bytes()
    switch = Mock(wraps=context.switch_account)
    monkeypatch.setattr(context, 'switch_account', switch)
    s.fallback.start()
    assert s.window._switch_account('synthetic-target') is False
    assert not s.release.is_set() and s.worker.isRunning()
    switch.assert_not_called()  # 原账号的任何 lifecycle.stop 均尚未进入。
    assert context.account is previous and context.generation == generation
    assert manager.accounts_index_file.read_bytes() == original_index
    QMessageBox.information.assert_called_once()
    QMessageBox.information.reset_mock()
    s.release.set()
    _retirement.spin_until(lambda: s.controller.worker is None)
    assert s.window._switch_account('synthetic-target') is True
    assert context.account.active_account_id == 'synthetic-target'
    assert context.generation == generation + 1
    assert manager.read_index()['active_account_id'] == 'synthetic-target'
    s.policy.update_deployment.assert_called_once()
    s.reconnect.assert_not_called()
    QMessageBox.information.assert_not_called()
    QMessageBox.warning.assert_not_called()
