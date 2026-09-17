# 验证设置页只提供采集排错选项，保存时不重启同步或改写旧策略。
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from src.features.settings.page import _build_capture_diagnostics_card
from src.ui.controllers import inventory_sync_controller as controller


def test_settings_card_removes_manual_sync_policy_controls():
    app = QApplication.instance() or QApplication([])
    titles = []
    def make_card(title):
        titles.append(title)
        widget = QWidget()
        widget.setLayout(QVBoxLayout())
        return widget
    saves = []
    host = SimpleNamespace(
        _card=make_card,
        _get_sync_settings=lambda: {"raw_capture_enabled": True, "capture_device_id": ""},
        _save_capture_diagnostics=lambda: saves.append(True),
        work_mode_controller=SimpleNamespace(set_raw_capture_draft=lambda _enabled: None),
    )
    card = _build_capture_diagnostics_card(host)
    try:
        labels = [label.text() for label in card.findChildren(QLabel)]
        buttons = [button.text() for button in card.findChildren(QPushButton)]
        assert titles == ["采集排错"]
        assert "保存排错设置" not in buttons and "打开原始数据目录" in buttons
        assert not {"背包获取方式:", "内容稳定等待:", "历史快照保留:"}.intersection(labels)
        assert "清理历史快照" not in buttons
        assert host._sync_raw_capture_toggle.isChecked()
        assert host._sync_capture_device_edit.width() == 360
        host._sync_capture_device_edit.setText("fixture-device")
        host._sync_capture_device_edit.editingFinished.emit()
        assert saves == [True]
        host._sync_raw_capture_toggle.click()
        assert saves == [True, True]
    finally:
        card.close()
        app.processEvents()


def test_save_diagnostics_preserves_packet_settings_and_running_sync(monkeypatch):
    original = {"raw_capture_enabled": False, "inventory_settle_seconds": 7,
                "inventory_snapshot_retention_count": 9, "inventory_sync_method": "gamepad"}
    saves = []
    def forbidden():
        raise AssertionError("saving diagnostics must not restart capture")
    host = SimpleNamespace(
        _account_settings=SimpleNamespace(load=lambda _key: dict(original),
                                         save=lambda key, values: saves.append(values) or values),
        _sync_raw_capture_toggle=SimpleNamespace(isChecked=lambda: True),
        _sync_capture_device_edit=SimpleNamespace(text=lambda: "device"),
        _inventory_sync_service=SimpleNamespace(is_running=True),
        _stop_inventory_sync=forbidden, _start_inventory_sync=forbidden,
        operation_entry=lambda *args: True,
    )
    monkeypatch.setattr(controller.QMessageBox, "information", lambda *args: None)
    result = controller._save_capture_diagnostics(host)
    assert len(saves) == 1
    assert result == {**original, "raw_capture_enabled": True, "capture_device_id": "device"}
