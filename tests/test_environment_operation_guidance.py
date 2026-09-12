# 仅用临时模式配置和假工作线程验证操作引导、取消与实际缺项。
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QMessageBox

from src.services.equipment_plugin_deployment import EquipmentPluginDeploymentError
from src.ui.controllers import environment_controller as env
from src.ui.controllers import mod_loader_controller as loader
from tests.test_environment_controller_plugin_deployment import _window
from tests.test_mod_loader_controller import _window as loader_window


@contextmanager
def diagnostic(window, kind):
    window.work_mode_service.select_mode('low' if kind == 'core' else 'developer', risk_confirmed=True)
    window.app_context.paths.app_dir = window.app_context.paths.root
    window._mod_plugin_loading_service.snapshot = MagicMock()
    window._show_nte_core_diagnostic_report = MagicMock()
    window._show_dwmapi_diagnostic_report = MagicMock()
    with patch.object(env, 'WorkerThread') as worker, patch.object(env, 'asdict', return_value={
        'loader_path': Path('fixture-loader'), 'payload_path': Path('fixture-payload'),
    }):
        (env._diagnose_nte_core if kind == 'core' else env._diagnose_dwmapi)(window)
        yield worker.return_value


@pytest.mark.parametrize('entry', [env._deploy_equipment_plugin, env._open_npcap_download,
    env._show_npcap_status, env._diagnose_nte_core, env._diagnose_dwmapi])
def test_denied_entry_returns_before_any_probe_worker_or_url(tmp_path, entry):
    window = _window(tmp_path)
    window.operation_entry = MagicMock(return_value=False)
    window._open_url = MagicMock()
    with patch.object(env, 'game_process_running') as running, patch.object(env, 'npcap_installation_present') as npcap, \
            patch.object(env, 'WorkerThread') as worker, patch.object(env.QMessageBox, 'question') as question:
        entry(window)
    for operation in (running, npcap, worker, question, window._open_url, window.operation_unavailable):
        operation.assert_not_called()
    window.operation_entry.assert_called_once()


def test_loader_denied_entry_does_not_probe_confirm_or_start(tmp_path):
    window = loader_window(tmp_path)
    window.operation_entry = MagicMock(return_value=False)
    with patch.object(loader.QMessageBox, 'question') as question:
        loader.start_equipment_mod_loader(window)
    question.assert_not_called()
    window._mod_plugin_loading_service.snapshot.assert_not_called()
    window._mod_plugin_loading_service.start_loader.assert_not_called()


def test_missing_npcap_guides_detection_without_changing_allowed_mode(tmp_path):
    window = _window(tmp_path)
    window.work_mode_service.select_mode('low', risk_confirmed=True)
    before = window.work_mode_service.settings
    with patch.object(env, 'npcap_installation_present', return_value=False):
        env._show_npcap_status(window)
    assert window.work_mode_service.settings == before
    assert window.operation_unavailable.call_args.kwargs['target'] == 'detection'
    assert '未检测到 Npcap' in window.operation_unavailable.call_args.args[1]


def test_missing_proxy_guides_deployment_before_confirmation(tmp_path):
    window = _window(tmp_path)
    with patch.object(env, 'game_process_running', return_value=False), patch.object(env, 'packaged_plugin_dll',
            side_effect=EquipmentPluginDeploymentError('fixture proxy missing')), patch.object(env.QMessageBox, 'question') as question:
        env._deploy_equipment_plugin(window)
    question.assert_not_called()
    window.operation_unavailable.assert_called_once_with('部署游戏内组件', 'fixture proxy missing', target='deployment')


def test_missing_loader_guides_deployment_before_confirmation(tmp_path):
    window = loader_window(tmp_path)
    window._mod_plugin_loading_service.snapshot.return_value = SimpleNamespace(phase='missing_loader', detail='fixture loader missing')
    with patch.object(loader.QMessageBox, 'question') as question:
        loader.start_equipment_mod_loader(window)
    question.assert_not_called()
    window.operation_unavailable.assert_called_once_with('启动 Mod Loader', 'fixture loader missing', target='deployment')
    window._mod_plugin_loading_service.start_loader.assert_not_called()


@pytest.mark.parametrize('kind', ['core', 'dwmapi'])
@pytest.mark.parametrize('channel', ['error', 'result_ready'])
def test_explicit_diagnostic_failure_guides_once(tmp_path, kind, channel):
    window = _window(tmp_path)
    with diagnostic(window, kind) as worker:
        callback = getattr(worker, channel).connect.call_args.args[0]
        payload = 'fixture connection unavailable' if channel == 'error' else {'ok': False, 'error': 'fixture component missing'}
        callback(payload)
        callback(payload)
    window.operation_unavailable.assert_called_once()
    assert window.operation_unavailable.call_args.kwargs['target'] == 'detection'


@pytest.mark.parametrize('kind', ['core', 'dwmapi'])
@pytest.mark.parametrize('channel', ['error', 'result_ready'])
def test_auto_sync_toggle_does_not_suppress_manual_diagnostic_receipt(tmp_path, kind, channel):
    window = _window(tmp_path)
    with diagnostic(window, kind) as worker:
        window.work_mode_service.set_auto_sync_enabled(False)
        callback = getattr(worker, channel).connect.call_args.args[0]
        payload = 'fixture missing' if channel == 'error' else {'ok': False, 'error': 'fixture missing'}
        callback(payload)
        callback(payload)
    window.operation_unavailable.assert_called_once()


@pytest.mark.parametrize('kind', ['core', 'dwmapi'])
def test_mode_round_trip_does_not_revive_old_manual_diagnostic_receipt(tmp_path, kind):
    window = _window(tmp_path)
    with diagnostic(window, kind) as worker:
        mode = window.work_mode_service.settings.mode
        window.work_mode_service.select_mode('offline')
        window.work_mode_service.select_mode(mode, risk_confirmed=True)
        worker.error.connect.call_args.args[0]('stale failure')
    window.operation_unavailable.assert_not_called()


@pytest.mark.parametrize('kind', ['core', 'dwmapi'])
@pytest.mark.parametrize('change', ['account', 'generation', 'mode', 'replaced_worker'])
def test_stale_diagnostic_failure_does_not_prompt(tmp_path, kind, change):
    window = _window(tmp_path)
    with diagnostic(window, kind) as worker:
        if change == 'account':
            window.app_context.account.active_account_id = 'new-fixture'
        elif change == 'generation':
            window.app_context.generation += 1
        elif change == 'mode':
            window.work_mode_service.select_mode('offline')
        elif kind == 'core':
            window._nte_core_diagnostic_worker = MagicMock()
        else:
            window._dwmapi_diagnostic_worker = MagicMock()
        worker.error.connect.call_args.args[0]('cancelled stale operation')
    window.operation_unavailable.assert_not_called()


def test_refresh_keeps_busy_diagnostic_disabled(tmp_path):
    window = _window(tmp_path)
    window._equipment_plugin_status_label = None
    window._nte_core_diagnostic_button = MagicMock()
    window._nte_core_diagnostic_worker = SimpleNamespace(isRunning=lambda: True)
    env._refresh_equipment_plugin_status(window)
    window._nte_core_diagnostic_button.setEnabled.assert_called_once_with(False)


def test_loader_waiting_without_pipe_remains_diagnostic_information(tmp_path):
    window = _window(tmp_path)
    with diagnostic(window, 'dwmapi') as worker:
        worker.result_ready.connect.call_args.args[0]({'ok': True, 'loader': {'phase': 'running'}, 'pipe': {'state': 'missing'}})
    window.operation_unavailable.assert_not_called()
    window._show_dwmapi_diagnostic_report.assert_called_once()


def test_revoked_loader_guard_does_not_misreport_missing_component(tmp_path):
    window = loader_window(tmp_path)

    def revoked(**_kwargs):
        window.work_mode_service.select_mode('offline')
        raise PermissionError('operation revoked')

    window._mod_plugin_loading_service.start_loader.side_effect = revoked
    with patch.object(loader.QMessageBox, 'question', return_value=QMessageBox.Yes):
        loader.start_equipment_mod_loader(window)
    window.operation_unavailable.assert_not_called()


def test_revoked_deployment_guard_does_not_misreport_missing_component(tmp_path):
    window = _window(tmp_path)

    def revoked():
        window.work_mode_service.select_mode('offline')
        raise PermissionError('operation revoked')

    window._mod_plugin_loading_service.ensure_proxy_deployment_allowed.side_effect = revoked
    with patch.object(env, 'game_process_running', return_value=False):
        env._deploy_equipment_plugin(window)
    window.operation_unavailable.assert_not_called()
