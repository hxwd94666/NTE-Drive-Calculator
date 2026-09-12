# 以本机临时模式设置验证 Loader 界面的授权、记录与统一清理入口。
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QMessageBox

from src.services.mod_plugin_loading_service import ModPluginLoadingError
from src.services.work_mode_service import WorkModeService
from src.ui.controllers.mod_loader_controller import (
    deactivate_equipment_plugin_loading_method, equipment_plugin_loading_method_changed,
    start_equipment_mod_loader, stop_equipment_mod_loader,
)


def _window(tmp_path):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    policy.set_game_executable(str(tmp_path / "HTGame.exe"))
    policy.set_cleanup_pending(False)
    policy.update_deployment({"deployed_sha256": "a" * 64, "workspace_path": str(tmp_path / "workspace")})
    result = SimpleNamespace(
        workspace_path=tmp_path / "workspace",
        runtime=SimpleNamespace(process_id=123),
        removed_proxy=SimpleNamespace(sha256="a" * 64),
    )
    service = SimpleNamespace(
        pending_workspace_cleanup_path=tmp_path / "workspace", active_payload_sha256="b" * 64,
        start_loader=MagicMock(return_value=result), stop_loader=MagicMock(),
        snapshot=MagicMock(return_value=SimpleNamespace(phase="stopped")),
    )
    return SimpleNamespace(
        operation_entry=MagicMock(side_effect=lambda capability, _label: policy.allowed(capability)),
        operation_unavailable=MagicMock(),
        work_mode_service=policy, _mod_plugin_loading_service=service,
        work_mode_runtime=SimpleNamespace(invalidate=MagicMock(), save_pending_deployment=MagicMock()),
        work_mode_controller=SimpleNamespace(cleanup=MagicMock()),
        app_context=SimpleNamespace(paths=SimpleNamespace(root=tmp_path, config_dir=tmp_path / "config")),
        _equipment_plugin_loading_method_combo=SimpleNamespace(currentData=lambda: "loader"),
        _refresh_equipment_plugin_status=MagicMock(),
        _ui_preferences={"equipment_plugin_risk_acknowledged": True, "equipment_plugin_deployed_sha256": "account-hash"},
        _save_ui_preferences=MagicMock(),
    )


def test_loader_uses_local_record_and_config_paths_without_account_writes(tmp_path):
    window = _window(tmp_path)
    with patch("src.ui.controllers.mod_loader_controller.QMessageBox.question", return_value=QMessageBox.Yes), patch(
        "src.ui.controllers.mod_loader_controller.QMessageBox.information",
    ):
        start_equipment_mod_loader(window)
    arguments = window._mod_plugin_loading_service.start_loader.call_args.kwargs
    assert arguments["game_executable_path"] == window.work_mode_service.settings.game_executable
    assert arguments["recorded_proxy_sha256"] == "a" * 64
    assert arguments["proxy_backup_directory"] == tmp_path / "config" / "component-backups"
    assert "recorded_proxy_registry_value_before" not in arguments
    reopened = WorkModeService(tmp_path / "mode.json")
    assert reopened.deployment_record["loading_method"] == "loader"
    assert reopened.deployment_record["loader_payload_sha256"] == "b" * 64
    assert reopened.deployment_record["workspace_path"] == str(tmp_path / "workspace")
    assert window._ui_preferences["equipment_plugin_deployed_sha256"] == "account-hash"
    window._save_ui_preferences.assert_not_called()


def test_loader_failure_persists_pending_registration_for_next_launch(tmp_path):
    window = _window(tmp_path)
    window._mod_plugin_loading_service.start_loader.side_effect = ModPluginLoadingError("fixture failed after registration")
    with patch("src.ui.controllers.mod_loader_controller.QMessageBox.question", return_value=QMessageBox.Yes), patch(
        "src.ui.controllers.mod_loader_controller.QMessageBox.warning",
    ):
        start_equipment_mod_loader(window)
    reopened = WorkModeService(tmp_path / "mode.json")
    assert reopened.settings.pending_cleanup
    assert reopened.deployment_record["workspace_path"] == str(tmp_path / "workspace")
    window._save_ui_preferences.assert_not_called()


def test_loader_failure_without_owned_registration_does_not_invent_pending(tmp_path):
    window = _window(tmp_path)
    window._mod_plugin_loading_service.pending_workspace_cleanup_path = None
    window._mod_plugin_loading_service.start_loader.side_effect = ModPluginLoadingError("unknown proxy")
    with patch("src.ui.controllers.mod_loader_controller.QMessageBox.question", return_value=QMessageBox.Yes), patch(
        "src.ui.controllers.mod_loader_controller.QMessageBox.warning",
    ):
        start_equipment_mod_loader(window)
    assert not window.work_mode_service.settings.pending_cleanup
    assert window.work_mode_service.deployment_record["deployed_sha256"] == "a" * 64


def test_offline_loader_ignores_old_account_risk_acknowledgement(tmp_path):
    window = _window(tmp_path)
    window.work_mode_service.select_mode("offline")
    with patch("src.ui.controllers.mod_loader_controller.QMessageBox.warning"), patch(
        "src.ui.controllers.mod_loader_controller.QMessageBox.question",
    ) as question:
        start_equipment_mod_loader(window)
    question.assert_not_called()
    window._mod_plugin_loading_service.start_loader.assert_not_called()


def test_all_stop_buttons_request_the_same_cleanup_lifecycle(tmp_path):
    window = _window(tmp_path)
    deactivate_equipment_plugin_loading_method(window)
    stop_equipment_mod_loader(window)
    assert window.work_mode_controller.cleanup.call_count == 2
    window._mod_plugin_loading_service.stop_loader.assert_not_called()


def test_loading_method_is_saved_only_to_local_record(tmp_path):
    window = _window(tmp_path)
    equipment_plugin_loading_method_changed(window, 1)
    assert window.work_mode_service.deployment_record["loading_method"] == "loader"
    assert "equipment_plugin_loading_method" not in window._ui_preferences
    window._save_ui_preferences.assert_not_called()


def test_start_confirmation_preserves_unknown_files_and_explains_no_hot_unload(tmp_path):
    window = _window(tmp_path)
    with patch("src.ui.controllers.mod_loader_controller.QMessageBox.question", return_value=QMessageBox.No) as question:
        start_equipment_mod_loader(window)
    message = question.call_args.args[2]
    assert "来源未知或已修改的文件会保留" in message
    assert "不代表游戏中的 DLL 已卸载" in message
    assert "账号存储" not in message
