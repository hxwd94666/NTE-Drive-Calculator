# 验证补抓引导只在抓包真正就绪后提示登录且正确处理关闭和取消。
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from auto_sync_ui_fixture import application, dispose

from src.features.home.sync_retry_dialog import SyncRetryDialog
from src.services.inventory_sync_service import InventorySyncState


class Controller(QObject):
    state_changed = Signal(object)
    preparation_changed = Signal(object)
    preparation_state = None

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        window._inventory_sync_service = None
        self.starts = self.cancels = 0

    def restart(self):
        self.starts += 1

    def cancel_restart(self):
        self.cancels += 1


@pytest.fixture
def dialog():
    app = application()
    parent = QWidget()
    parent.battle_report_controller = SimpleNamespace(is_running=lambda: False)
    controller = Controller(parent)
    view = SyncRetryDialog(parent, controller=controller)
    yield view, controller, app
    view.context_changed()
    dispose(parent)


def test_login_prompt_requires_capturing_not_starting_or_old_snapshot(dialog):
    view, c, _app = dialog
    view.begin.click()
    assert c.starts == 1
    for phase in ("starting", "waiting"):
        c.state_changed.emit(InventorySyncState(phase=phase, last_snapshot_id=42, message="等待网络"))
        assert "现在可以进入游戏" not in view.detail.text()
    c.state_changed.emit(InventorySyncState(phase="waiting", capturing=True, last_snapshot_id=42))
    assert "现在请重新登录游戏" in view.detail.text()
    view.reject()
    assert not c.cancels


def test_cancel_preparation_only_cancels_this_restart(dialog):
    view, c, _app = dialog
    view.begin.click()
    view.dismiss.click()
    assert c.cancels == 1


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('action', ['close', 'escape'])
def test_closing_pending_retry_keeps_background_sync(dialog, native, action):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    _view, c, app = dialog
    view = SyncRetryDialog(c.window, controller=c, native=native)
    view.show()
    view.begin.click()
    if action == 'close':
        view.close()
    else:
        QTest.keyClick(view, Qt.Key_Escape)
    app.processEvents()
    assert c.starts == 1 and c.cancels == 0
    dispose(view)


def test_close_guide_before_confirmation_does_not_stop_old_sync(dialog):
    view, c, _app = dialog
    view.reject()
    assert not c.starts and not c.cancels


def test_collecting_is_not_completed_and_context_change_does_not_cancel_new_owner(dialog):
    view, c, _app = dialog
    view.begin.click()
    c.state_changed.emit(InventorySyncState(phase="collecting", source_snapshot_ready=True))
    assert "同步完成" not in view.detail.text()
    view.context_changed()
    assert not c.cancels


def test_battle_started_after_opening_guide_prevents_restart(dialog):
    view, c, _app = dialog
    c.window.battle_report_controller.is_running = lambda: True
    view.begin.click()
    assert not c.starts and "先结束战报" in view.detail.text()


def test_native_retry_uses_the_same_login_guidance(dialog):
    _view, c, _app = dialog
    native = SyncRetryDialog(c.window, controller=c, native=True)
    assert "登录界面" in native.detail.text()
    native.begin.click()
    c.state_changed.emit(InventorySyncState(phase="waiting", capturing=True, message="等待组件"))
    assert "登录界面" in native.detail.text()
    c.state_changed.emit(InventorySyncState(phase="listening", source_snapshot_ready=True))
    assert "同步完成" in native.detail.text()
    dispose(native)
