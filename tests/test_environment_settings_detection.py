# 用真实设置卡片验证升级后自动检测、原生组件状态和过期回调，不访问真实游戏。
import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from src.features.settings.page import _build_environment_card
from src.services.work_mode_service import WorkModeService
from src.ui.controllers import environment_controller as environment
from tests.test_native_plugin_bundle import make_bundle


@pytest.fixture
def settings_card(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root, _ = make_bundle(tmp_path)
    window = QWidget()
    window.app_context = SimpleNamespace(
        paths=SimpleNamespace(root=root), account=SimpleNamespace(active_account_id="fixture"), generation=1,
    )
    policy = window.work_mode_service = WorkModeService(tmp_path / "work-mode.json")
    policy.update_deployment({"loading_method": "proxy"})
    window._mod_plugin_loading_service = SimpleNamespace(snapshot=Mock())
    window._refresh_equipment_plugin_status = lambda: environment._refresh_equipment_plugin_status(window)
    window._detect_equipment_plugin_game_executable = lambda: environment._detect_equipment_plugin_game_executable(window)
    for name in ("_open_npcap_download", "_show_npcap_status", "_diagnose_nte_core",
                 "_equipment_plugin_loading_method_changed", "_select_equipment_plugin_game_executable",
                 "_activate_equipment_plugin_loading_method", "_deactivate_equipment_plugin_loading_method"):
        window.__dict__[name] = Mock()

    def card(_title):
        widget = QWidget(window)
        QVBoxLayout(widget)
        return widget

    window._card = card
    monkeypatch.setattr(environment, "npcap_installation_present", lambda: False)
    worker = Mock()
    worker.isRunning.return_value = False
    monkeypatch.setattr(environment, "WorkerThread", Mock(return_value=worker))
    _build_environment_card(window)
    yield window, worker
    window.close()
    window.deleteLater()
    app.processEvents()


def test_existing_proxy_preference_refreshes_actual_native_controls(settings_card):
    window, _worker = settings_card
    assert window._equipment_plugin_loading_method_combo.currentData() == "native-capture"
    assert window._equipment_plugin_primary_button.text() == "部署原生组件"
    assert window._equipment_plugin_status_label.text()
    assert window._equipment_plugin_bundle_label.text() == "原生组件整包已核对"
    # Display adaptation does not rewrite the user's deployment ownership record.
    assert window.work_mode_service.deployment_record == {"loading_method": "proxy"}


def test_detect_success_updates_real_settings_card_without_missing_widget(settings_card, tmp_path):
    window, worker = settings_card
    executable = tmp_path / "game" / "HTGame.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")
    window._equipment_plugin_detect_button.click()
    assert not window._equipment_plugin_detect_button.isEnabled()
    worker.result_ready.connect.call_args.args[0]([executable])
    assert window._equipment_plugin_detect_button.isEnabled()
    assert window.work_mode_service.settings.game_executable == str(executable)
    assert window._equipment_plugin_game_executable_edit.text() == str(executable)
    assert window._equipment_plugin_primary_button.text() == "部署原生组件"
    assert window._equipment_plugin_status_label.text()


def test_detection_does_not_write_after_account_switch(settings_card):
    window, worker = settings_card
    window._equipment_plugin_detect_button.click()
    window.app_context.generation += 1
    worker.result_ready.connect.call_args.args[0](["C:/different/HTGame.exe"])
    assert window._equipment_plugin_detect_button.isEnabled()
    assert window.work_mode_service.settings.game_executable == ""
    assert window._equipment_plugin_game_executable_edit.text() == ""
