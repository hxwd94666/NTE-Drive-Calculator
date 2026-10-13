# 验证极速装配关闭与全局停止共用取消信号，完成收尾不误取消或占用热键。
import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QProgressDialog, QPushButton, QWidget

from src.features.inventory.equipment import assembly_controller as controller


class PendingWorker(QObject):
    result_ready = Signal(object)
    error = Signal(str)

    def __init__(self, target, parent=None):
        super().__init__(parent)
        self.target = target
        self.running = False

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def apply_session(app, monkeypatch, tmp_path):
    owner = QWidget()
    owner.operation_entry = lambda *_: True
    owner.operation_unavailable = Mock()
    owner._inventory_sync_service = SimpleNamespace(is_running=True)
    owner.user_database_path = tmp_path / "unused.sqlite3"
    owner.global_hotkey_manager = SimpleNamespace(
        active_owner=None, configuration=SimpleNamespace(stop="F12"), start=Mock(), stop=Mock(),
    )
    service_factory = Mock()
    monkeypatch.setattr(controller, "BulkEquipmentApplyService", service_factory)
    monkeypatch.setattr(controller, "WorkerThread", PendingWorker)
    monkeypatch.setattr(controller.QMessageBox, "warning", Mock())
    monkeypatch.setattr(controller.QMessageBox, "information", Mock())
    owner.show()
    controller._start_nte_core_equipment_apply(owner, ["测试角色"])
    dialog = owner.findChild(QProgressDialog)
    cancel = service_factory.call_args.kwargs["cancel_event"]
    yield owner, dialog, cancel
    if hasattr(owner, "_equipment_apply_worker"):
        owner._equipment_apply_worker.error.emit("收尾")
    owner.close()
    owner.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("action", ["close", "escape"])
def test_progress_has_no_stop_button_and_close_cancels_without_reopening(apply_session, app, action):
    owner, dialog, cancel = apply_session
    assert not dialog.findChildren(QPushButton)
    assert not cancel.is_set()
    if action == "escape":
        QTest.keyClick(dialog, Qt.Key_Escape)
    else:
        dialog.close()
    assert cancel.is_set()
    QTest.qWait(120)
    app.processEvents()
    assert not dialog.isVisible()
    # Cancelling the UI does not kill the worker or the shared sync service.
    assert owner._equipment_apply_worker.isRunning()
    assert owner._inventory_sync_service.is_running


def test_global_stop_sets_the_same_token_then_hides_progress(apply_session, app):
    owner, dialog, cancel = apply_session
    callback = owner.global_hotkey_manager.start.call_args.kwargs["on_stop"]
    callback()
    assert cancel.is_set()
    QTest.qWait(120)
    app.processEvents()
    assert not dialog.isVisible()


@pytest.mark.parametrize("outcome", ["result", "error"])
def test_completion_releases_only_its_hotkey_owner_without_cancelling(apply_session, outcome):
    owner, _dialog, cancel = apply_session
    worker = owner._equipment_apply_worker
    if outcome == "result":
        worker.result_ready.emit({"preflight_errors": [{"role_name": "测试角色", "error": "测试缺项"}]})
    else:
        worker.error.emit("测试错误")
    assert not cancel.is_set()
    owner.global_hotkey_manager.stop.assert_called_once_with(owner="fast_equipment_apply")
    # Teardown must not emit another outcome for a completed worker.
    del owner._equipment_apply_worker


def test_other_hotkey_owner_prevents_starting_a_competing_task(app, monkeypatch):
    owner = QWidget()
    owner.operation_entry = lambda *_: True
    owner._inventory_sync_service = SimpleNamespace(is_running=True)
    owner.global_hotkey_manager = SimpleNamespace(active_owner="scanning")
    start = Mock()
    monkeypatch.setattr(controller, "BulkEquipmentApplyService", start)
    monkeypatch.setattr(controller.QMessageBox, "information", Mock())
    controller._start_nte_core_equipment_apply(owner, ["测试角色"])
    start.assert_not_called()
    assert not owner.findChildren(QProgressDialog)
    owner.close()


def test_compact_progress_keeps_status_and_stop_hint_readable(apply_session, app):
    _owner, dialog, cancel = apply_session
    long_status = "正在等待角色装配后取得完整背包观测；等待期间已保存的数据保持不变。"
    dialog.setLabelText(long_status + "\n关闭此窗口或按 F12 停止后续装配。")
    app.processEvents()
    label = dialog.findChild(QLabel)
    progress = dialog.findChild(QProgressBar)
    assert long_status in label.text()
    assert "F12" in label.text()
    assert label.wordWrap()
    assert label.isVisible()
    assert progress.isVisible()
    assert dialog.rect().contains(label.geometry())
    assert dialog.rect().contains(progress.geometry())
    assert not cancel.is_set()
