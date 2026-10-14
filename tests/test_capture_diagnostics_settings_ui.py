# 验证保存排错偏好不重启同步，也不改写已有背包策略。
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from src.ui.controllers import inventory_sync_controller as controller


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
