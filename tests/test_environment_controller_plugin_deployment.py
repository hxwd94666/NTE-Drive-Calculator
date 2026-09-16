# 验证部署装备插件前的游戏进程提示和精简确认文案。
from pathlib import Path

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QMessageBox

from src.services.equipment_plugin_deployment import PluginDeployment, PluginDeploymentPendingCleanup
from src.services.work_mode_service import WorkModeService
from src.ui.controllers.environment_controller import (
    _deploy_equipment_plugin, _diagnose_dwmapi, _diagnose_nte_core,
    _refresh_equipment_plugin_status,
)


def _window(tmp_path):
    policy = WorkModeService(tmp_path / "work-mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    policy.set_game_executable("C:/game/HTGame.exe")
    return SimpleNamespace(
        operation_entry=MagicMock(side_effect=lambda capability, _label: policy.allowed(capability)),
        operation_unavailable=MagicMock(),
        _equipment_plugin_consent=SimpleNamespace(isChecked=lambda: True),
        _equipment_plugin_game_executable_edit=SimpleNamespace(
            text=lambda: "C:/game/HTGame.exe",
        ),
        _mod_plugin_loading_service=SimpleNamespace(
            ensure_proxy_deployment_allowed=MagicMock(),
        ),
        app_context=SimpleNamespace(paths=SimpleNamespace(root=tmp_path, config_dir=tmp_path / "config"),
                                    account=SimpleNamespace(active_account_id="fixture"), generation=1),
        work_mode_service=policy,
        work_mode_runtime=SimpleNamespace(save_deployment=MagicMock(), save_pending_deployment=MagicMock()),
        _equipment_plugin_status_label=MagicMock(),
        _ui_preferences={"equipment_plugin_risk_acknowledged": True, "equipment_plugin_deployed_sha256": "old-account-record"},
    )


@patch("src.ui.controllers.environment_controller.QMessageBox.warning")
@patch("src.ui.controllers.environment_controller.game_process_running", return_value=True)
def test_deploy_stops_with_clear_message_while_game_is_running(game_running, warning, tmp_path):
    window = _window(tmp_path)

    _deploy_equipment_plugin(window)

    game_running.assert_called_once_with()
    warning.assert_called_once_with(
        window,
        "部署装备插件",
        "检测到游戏正在运行。\n请完全退出游戏后再部署插件。",
    )
    window._mod_plugin_loading_service.ensure_proxy_deployment_allowed.assert_not_called()


@patch("src.ui.controllers.environment_controller.QMessageBox.question")
@patch("src.ui.controllers.environment_controller.packaged_plugin_dll", return_value=Path("plugin.dll"))
@patch("src.ui.controllers.environment_controller.game_process_running", return_value=False)
def test_deploy_confirmation_keeps_only_action_and_backup_information(
    _game_running,
    _plugin,
    question,
    tmp_path,
):
    question.return_value = QMessageBox.No
    window = _window(tmp_path)

    _deploy_equipment_plugin(window)

    assert question.call_args.args[2] == (
        "将部署装备插件到所选游戏目录。\n"
        "已有同名文件会自动备份。\n\n"
        "是否继续？"
    )


def test_account_risk_checkbox_cannot_authorize_offline_deployment(tmp_path):
    window = _window(tmp_path)
    window.work_mode_service.select_mode("offline")
    with patch("src.ui.controllers.environment_controller.QMessageBox.warning"), patch(
        "src.ui.controllers.environment_controller.game_process_running",
    ) as running:
        _deploy_equipment_plugin(window)
    running.assert_not_called()


def test_manual_deployment_pending_error_is_given_to_local_lifecycle(tmp_path):
    window = _window(tmp_path)
    deployed = PluginDeployment(Path("C:/game/HTGame.exe"), tmp_path / "dwmapi.dll", None, "a" * 64, tmp_path / "workspace")
    error = PluginDeploymentPendingCleanup("fixture pending", deployment=deployed)
    with patch("src.ui.controllers.environment_controller.QMessageBox.warning"), patch(
        "src.ui.controllers.environment_controller.QMessageBox.question", return_value=QMessageBox.Yes,
    ), patch("src.ui.controllers.environment_controller.game_process_running", return_value=False), patch(
        "src.ui.controllers.environment_controller.packaged_plugin_dll", return_value=tmp_path / "dwmapi.dll",
    ), patch("src.ui.controllers.environment_controller.deploy_plugin", side_effect=error) as deploy:
        _deploy_equipment_plugin(window)
    window.work_mode_runtime.save_pending_deployment.assert_called_once_with(error)
    assert deploy.call_args.kwargs["game_executable_path"] == window.work_mode_service.settings.game_executable
    assert window._ui_preferences["equipment_plugin_deployed_sha256"] == "old-account-record"


def test_refresh_buttons_remain_clickable_for_guidance_in_every_mode(tmp_path):
    window = _window(tmp_path)
    window._equipment_plugin_status_label = None
    window._equipment_plugin_primary_button = MagicMock()
    window._nte_core_diagnostic_button = MagicMock()
    window._dwmapi_diagnostic_button = MagicMock()
    window._equipment_plugin_stop_button = MagicMock()
    expected = {mode: (True, True, True) for mode in ("offline", "low", "medium", "developer")}
    for mode, values in expected.items():
        window.work_mode_service.select_mode(mode, risk_confirmed=mode != "offline")
        _refresh_equipment_plugin_status(window)
        for button, enabled in zip((window._equipment_plugin_primary_button, window._nte_core_diagnostic_button,
                                    window._dwmapi_diagnostic_button), values):
            button.setEnabled.assert_called_with(enabled)
    window._equipment_plugin_stop_button.setText.assert_called_with("清理游戏目录")


@pytest.mark.parametrize("mode", ["offline", "low", "medium", "developer"])
def test_raw_diagnostics_available_in_every_mode(tmp_path, mode):
    from src.services.mod_plugin_loading_service import ModPluginLoadingError
    window = _window(tmp_path)
    window.work_mode_service.select_mode(mode, risk_confirmed=mode != "offline")
    window._mod_plugin_loading_service.snapshot = MagicMock(side_effect=ModPluginLoadingError("not running"))
    with patch("src.ui.controllers.environment_controller.WorkerThread") as worker:
        _diagnose_dwmapi(window)
    worker.assert_called_once()
    worker.return_value.start.assert_called_once()


def test_readonly_diagnostic_worker_remains_allowed_after_switch_to_offline(tmp_path):
    window = _window(tmp_path)
    window.app_context.paths.app_dir = tmp_path
    window.work_mode_service.select_mode("low", risk_confirmed=True)
    with patch("src.ui.controllers.environment_controller.WorkerThread") as worker, patch(
        "src.ui.controllers.environment_controller.collect_nte_core_diagnostics",
    ) as collect:
        _diagnose_nte_core(window)
        window.work_mode_service.select_mode("offline")
        worker.call_args.kwargs["target"]()
    collect.assert_called_once_with(cwd=tmp_path)
