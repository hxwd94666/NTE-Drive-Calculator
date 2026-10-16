# 验证首次选根、迁入副作用门禁和启动失败的真实 Qt owner 退场。
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QWidget, QFileDialog, QMessageBox

from src.integrations.distribution.data_roots import write_record
from src.integrations.distribution.lock import DataRootLease
from src.integrations.distribution.metadata import Distribution, DistributionError
from src.integrations.distribution.machine_revalidation import MigratedMachineGate
from src.services.work_modes.service import WorkModeService


def test_failed_visible_startup_waits_for_owner_with_data_lease(tmp_path):
    from src.ui.gui_startup import retire_failed_startup_window
    app = QApplication.instance() or QApplication([])
    events = []
    class Worker(QThread):
        def run(self):
            self.msleep(100)
    class Window(QWidget):
        def closeEvent(self, event):
            if worker.isRunning():
                events.append("ignored")
                event.ignore()
                QTimer.singleShot(25, self.close)
            else:
                events.append("closed")
                event.accept()
    worker, window = Worker(), Window()
    worker.start()
    window.show()
    app.processEvents()
    with DataRootLease(tmp_path):
        def confirm_held():
            with pytest.raises(DistributionError):
                DataRootLease(tmp_path)
            events.append("lease-held")
        QTimer.singleShot(20, confirm_held)
        retire_failed_startup_window(window)
        assert not worker.isRunning() and not window.isVisible()
        assert events[0] == "ignored" and "lease-held" in events and events[-1] == "closed"
    worker.wait()
    window.deleteLater()
    app.processEvents()
    with DataRootLease(tmp_path):
        pass


def test_first_external_choice_cancel_reopen_and_missing_locator(tmp_path, monkeypatch):
    from src.ui import startup_data_selection as ui
    from src.integrations.distribution import data_roots
    app = QApplication.instance() or QApplication([])
    distribution = Distribution(distribution="portable", update_channel="platform", data_policy="external",
                                data_root_key="fixture", build_id="fixture")
    package = tmp_path / "app"
    package.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(ui, "resolve_application_roots", lambda: (package, package))
    monkeypatch.setattr(ui, "load_distribution", lambda _: distribution)
    monkeypatch.setattr(ui, "verify_release", lambda *_: object())
    choice = Mock(return_value=QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, "question", choice)
    folder = Mock(return_value="")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", folder)
    assert ui.choose_first_external_root() is False
    assert not (tmp_path / "local").exists()
    choice.return_value = QMessageBox.StandardButton.Ok
    assert ui.choose_first_external_root() is False
    root = tmp_path / "external"
    folder.return_value = str(package / "wrong")
    with pytest.raises(DistributionError, match="之外"):
        ui.choose_first_external_root()
    assert not (package / "wrong").exists()
    folder.return_value = str(root)
    assert ui.choose_first_external_root() is True
    assert not (root / "accounts").exists()
    choice.reset_mock()
    assert ui.choose_first_external_root() is True
    choice.assert_not_called()
    data_roots._locator(distribution, None).unlink()
    with pytest.raises(DistributionError, match="遗失"):
        ui.choose_first_external_root()
    choice.assert_not_called()
    assert data_roots.select_external_data_root(distribution, root) == root
    app.processEvents()


def test_migrated_machine_gate_preserves_preferences_until_explicit_confirmation(tmp_path, monkeypatch):
    from src.ui.migrated_machine_confirmation import confirm_migrated_machine
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "config/work_mode.json"
    policy = WorkModeService(path)
    policy.select_mode("medium", risk_confirmed=True)
    policy.set_auto_sync_enabled(True)
    policy.set_component_auto_ready(True)
    policy.set_game_executable("fixture/HTGame.exe")
    policy.update_deployment({"native_workspace_root": "old/workspace"})
    original = path.read_bytes()
    write_record(tmp_path / "maintenance/last-import.json", {"machine_paths_require_revalidation": True})
    policy = WorkModeService(path, machine_gate=MigratedMachineGate(tmp_path))
    assert policy.machine_revalidation_required
    assert not policy.allowed("native_sync", automatic=True)
    assert not policy.allowed("native_load")
    assert policy.allowed("local") and path.read_bytes() == original
    prompt = Mock(return_value=QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, "question", prompt)
    confirm_migrated_machine(None, policy, SimpleNamespace(game_path_valid=True))
    assert policy.machine_revalidation_required
    prompt.return_value = QMessageBox.StandardButton.Yes
    confirm_migrated_machine(None, policy, SimpleNamespace(game_path_valid=True))
    assert not policy.machine_revalidation_required
    assert policy.allowed("native_sync", automatic=True)
    assert path.read_bytes() == original
    assert not WorkModeService(path, machine_gate=MigratedMachineGate(tmp_path)).machine_revalidation_required
    assert not MigratedMachineGate(tmp_path / "ordinary-upgrade").pending
    app.processEvents()


def test_migrated_runtime_tick_never_deploys_connects_or_cleans(tmp_path, monkeypatch):
    from src.services.work_modes import runtime as module
    write_record(tmp_path / "maintenance/last-import.json", {"machine_paths_require_revalidation": True})
    policy = WorkModeService(tmp_path / "config/work_mode.json", machine_gate=MigratedMachineGate(tmp_path))
    policy.select_mode("medium", risk_confirmed=True)
    policy.set_cleanup_pending(True)
    native = Mock(battle_active=False)
    runtime = module.WorkModeRuntime(policy=policy, native_session=native, loader=Mock(),
                                    application_root=tmp_path, config_dir=tmp_path / "config")
    monkeypatch.setattr(module, "create_bundled_analysis_client", lambda **_: None)
    monkeypatch.setattr(module, "resolve_nte_core_executable", lambda: Path("missing"))
    monkeypatch.setattr(module, "npcap_installation_present", lambda: False)
    monkeypatch.setattr(runtime, "discover", Mock())
    monkeypatch.setattr(runtime, "_validated_game_path", lambda _: None)
    cleanup, deploy = Mock(), Mock()
    monkeypatch.setattr(runtime, "_cleanup_components", cleanup)
    monkeypatch.setattr(runtime, "_automatic_deploy", deploy)
    runtime.tick(allow_connect=True)
    with pytest.raises(PermissionError, match="重检|检测确认"):
        runtime.cleanup()
    cleanup.assert_not_called()
    deploy.assert_not_called()
    native.inspect.assert_not_called()
    runtime.discover.assert_called_once_with(force=False, persist=False)
