# 验证隐藏保留后台 owner、取消退出可恢复，以及无托盘环境仍有可见入口。
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QMainWindow
from tests.support.auto_sync_ui_fixture import application

from src.ui.main_window.tray_controller import TrayController


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.closed = 0
        self.cancel_exit = False

    def closeEvent(self, event):
        self.closed += 1
        if self.cancel_exit:
            event.ignore()
        else:
            event.accept()


def test_hide_restore_and_explicit_exit():
    application()
    window = Window()
    tray = TrayController(window, available=lambda: True)
    try:
        window.show()
        with patch.object(tray.tray, 'showMessage'):
            tray.hide_window()
        assert not window.isVisible() and window.closed == 0
        tray.restore()
        assert window.isVisible() and window.closed == 0
        window.cancel_exit = True
        tray.hide_window()
        tray.request_exit()
        assert window.isVisible() and window.closed == 1
        window.cancel_exit = False
        tray.request_exit()
        assert not window.isVisible() and window.closed == 2
    finally:
        tray.close()
        window.cancel_exit = False
        window.close()
        window.deleteLater()
        QApplication.processEvents()


def test_missing_tray_keeps_taskbar_entry():
    application()
    window = Window()
    tray = TrayController(window, available=lambda: False)
    try:
        window.show()
        tray.hide_window()
        assert window.isVisible() and window.isMinimized() and window.closed == 0
        tray.restore()
        assert not window.isMinimized()
    finally:
        tray.close()
        window.close()
        window.deleteLater()
        QApplication.processEvents()
