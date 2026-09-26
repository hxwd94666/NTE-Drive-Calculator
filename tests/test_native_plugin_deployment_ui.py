# 验证新版组件设置入口保留 Loader 选择且不将其送入 D3D 部署。
import os
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QPushButton, QWidget

from src.ui.controllers.native_plugin_deployment_ui import refresh_native_plugin_status
from src.ui.controllers.mod_loader_controller import activate_equipment_plugin_loading_method
from src.services.work_mode_service import WorkModeService
from tests.test_native_plugin_bundle import make_bundle


@pytest.mark.parametrize('action, accepted', [
    ('取消', False), ('已退出游戏，继续部署', True),
])
def test_d3d_confirmation_leads_with_game_exit_and_defaults_to_cancel(monkeypatch, action, accepted):
    from src.ui.controllers.native_plugin_deployment_ui import _confirm_d3d_deployment

    app = QApplication.instance() or QApplication([])
    parent = QWidget()

    def inspect(dialog):
        assert dialog.objectName() == 'd3dDeploymentConfirmation'
        assert '完全退出游戏' in dialog.findChild(QLabel, 'd3dDeploymentExitWarning').text()
        assert 'd3d12.dll' in dialog.findChild(QLabel, 'd3dDeploymentChanges').text()
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        assert set(buttons) == {'取消', '已退出游戏，继续部署'}
        assert buttons['取消'].isDefault()
        buttons[action].click()
        return dialog.result()

    monkeypatch.setattr(QDialog, 'exec', inspect)
    try:
        assert _confirm_d3d_deployment(parent) is accepted
    finally:
        parent.close()
        app.processEvents()


def test_native_status_preserves_loader_and_routes_only_selected_entry(tmp_path):
    app = QApplication.instance() or QApplication([])
    root, _payload = make_bundle(tmp_path)
    window = QWidget()
    window.app_context = SimpleNamespace(paths=SimpleNamespace(root=root))
    window.work_mode_service = WorkModeService(tmp_path / "mode.json")
    combo = window._equipment_plugin_loading_method_combo = QComboBox(window)
    combo.addItem("代理", "proxy")
    combo.addItem("Loader", "loader")
    combo.setCurrentIndex(1)
    window._equipment_plugin_primary_button = QPushButton(window)
    window._equipment_plugin_status_label = QLabel(window)
    window._mod_plugin_loading_service = SimpleNamespace(inspect_native_workspace=lambda: SimpleNamespace(files_compatible=False, issues=("Loader 运行目录尚未准备组件",)))
    window._start_equipment_mod_loader = Mock()
    window._deploy_equipment_plugin = Mock()
    try:
        refresh_native_plugin_status(window)
        assert combo.isEnabled() and combo.currentData() == "loader" and combo.count() == 2
        activate_equipment_plugin_loading_method(window)
        window._start_equipment_mod_loader.assert_called_once()
        window._deploy_equipment_plugin.assert_not_called()
        combo.setCurrentIndex(combo.findData("native-capture"))
        activate_equipment_plugin_loading_method(window)
        window._deploy_equipment_plugin.assert_called_once()
        assert "组件未准备好" in window._equipment_plugin_status_label.text()
    finally:
        window.close()
        app.processEvents()


def test_native_manual_start_uses_same_mode_owner_and_skips_legacy_workspace(tmp_path, monkeypatch):
    from src.ui.controllers.mod_loader_controller import start_equipment_mod_loader
    from src.ui.controllers import native_plugin_deployment_ui as native_ui
    root, _ = make_bundle(tmp_path)
    policy = WorkModeService(tmp_path / 'mode.json')
    policy.select_mode('medium', risk_confirmed=True)
    policy.set_game_executable(str(tmp_path / 'HTGame.exe'))
    policy.set_cleanup_pending(False)
    policy.update_deployment({'loading_method': 'loader'})
    window = SimpleNamespace(
        app_context=SimpleNamespace(paths=SimpleNamespace(root=root)), work_mode_service=policy,
        work_mode_runtime=SimpleNamespace(start_native_loader=Mock()),
        _stop_inventory_sync=Mock(), character_profile_sync_controller=SimpleNamespace(request_stop=Mock()),
        operation_entry=lambda *_args: True, operation_unavailable=Mock(),
        _refresh_equipment_plugin_status=Mock(),
    )
    monkeypatch.setattr(native_ui.QMessageBox, 'information', Mock())
    start_equipment_mod_loader(window)
    window.work_mode_runtime.start_native_loader.assert_called_once_with()
    window.operation_unavailable.assert_not_called()


def test_manual_loader_wait_shows_close_launcher_prompt(monkeypatch):
    from src.ui.controllers import native_plugin_deployment_ui as native_ui
    from src.services.mod_plugin_loading_service import ModPluginLoadingWaiting
    window = SimpleNamespace(
        _stop_inventory_sync=Mock(),
        character_profile_sync_controller=SimpleNamespace(request_stop=Mock()),
        work_mode_runtime=SimpleNamespace(start_native_loader=Mock(
            side_effect=ModPluginLoadingWaiting('官方启动器仍在运行。'),
        )),
        _refresh_equipment_plugin_status=Mock(), operation_unavailable=Mock(),
    )
    warning = Mock()
    monkeypatch.setattr(native_ui.QMessageBox, 'warning', warning)
    native_ui.start_native_loader_from_settings(window)
    assert '退出启动器和游戏' in warning.call_args.args[2]
    window.operation_unavailable.assert_not_called()


def test_settings_deploy_routes_minimal_capture_bundle_without_legacy_proxy(tmp_path, monkeypatch):
    from src.ui.controllers import environment_controller as environment
    root, _ = make_bundle(tmp_path)
    policy = WorkModeService(tmp_path / 'mode.json')
    policy.select_mode('medium', risk_confirmed=True)
    policy.set_game_executable(str(tmp_path / 'HTGame.exe'))
    policy.set_cleanup_pending(False)
    window = SimpleNamespace(work_mode_service=policy, operation_entry=lambda *_args: True,
                             app_context=SimpleNamespace(paths=SimpleNamespace(root=root)))
    deploy = Mock()
    monkeypatch.setattr(environment, 'game_process_running', lambda: False)
    monkeypatch.setattr(environment, 'deploy_native_plugin_from_settings', deploy)
    environment._deploy_equipment_plugin(window)
    deploy.assert_called_once_with(window)
