# 持有本窗口托盘入口，隐藏不释放业务 owner，明确退出仍走主窗口收尾。
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMenu, QStyle, QSystemTrayIcon, QVBoxLayout

from src.app.window_geometry import fit_dialog_to_available_screen


def choose_close_action(window, tray_available: bool) -> str:
    """关闭选择仅属本次窗口交互，不保存偏好或提前撤销业务权限。"""
    box = QDialog(window)
    box.setWindowTitle("关闭窗口")
    box.setWindowFlag(Qt.WindowCloseButtonHint, True)
    box.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    box.setWindowModality(Qt.WindowModal)
    layout = QVBoxLayout(box)
    message = QLabel("直接退出将先确认未保存内容并结束后台任务；最小化后后台任务继续运行。", box)
    message.setTextFormat(Qt.PlainText)
    message.setWordWrap(True)
    layout.addWidget(message)
    buttons = QDialogButtonBox(box)
    layout.addWidget(buttons)
    choice = "cancel"

    def select(action):
        nonlocal choice
        choice = action
        box.accept()

    for text, action in (
        ("直接退出", "exit"),
        ("最小化到托盘" if tray_available else "最小化到任务栏", "minimize"),
    ):
        button = buttons.addButton(text, QDialogButtonBox.ActionRole)
        # 不用隐藏的取消按钮：原生标题栏关闭及 Esc 走 QDialog.reject；回车不默认退出。
        button.setAutoDefault(False)
        button.setDefault(False)
        button.clicked.connect(lambda _checked=False, action=action: select(action))
    box.setFocusPolicy(Qt.StrongFocus)
    box.setFocus()
    try:
        box.adjustSize()
        fit_dialog_to_available_screen(box)
        return choice if box.exec() == QDialog.Accepted else "cancel"
    finally:
        box.close()
        box.deleteLater()


class TrayController(QObject):
    def __init__(self, window, *, available=None, choose_close=None):
        super().__init__(window)
        self.window = window
        self._available = available or QSystemTrayIcon.isSystemTrayAvailable
        self._choose_close = choose_close or choose_close_action
        self._choosing_close = False
        self._notified = False
        self._closed = False
        self.menu = QMenu(window)
        self.menu.addAction("打开 Calc", self.restore)
        self.menu.addSeparator()
        self.menu.addAction("退出 Calc", self.request_exit)
        icon = window.windowIcon()
        if icon.isNull():
            icon = window.style().standardIcon(QStyle.SP_ComputerIcon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("NTE Drive Calc")
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._activated)
        if self._available():
            self.tray.show()

    def _confirm_close(self):
        if self._closed or self._choosing_close:
            return False
        self._choosing_close = True
        try:
            choice = self._choose_close(self.window, self._available())
            if self._closed:
                return False
            if choice == "minimize":
                self.hide_window()
            return choice == "exit"
        finally:
            self._choosing_close = False

    def request_window_close(self):
        if self._confirm_close():
            self.request_exit()

    def handle_close_event(self, event, *, saving_session=False):
        if not event.spontaneous() or saving_session:
            return False
        # Alt+F4 选择退出时继续原事件；异步退场重试的普通 close 不再次询问。
        if self._confirm_close():
            return False
        event.ignore()
        return True

    def hide_window(self):
        if self._closed:
            return
        if not self._available():
            # 托盘不可用时保留任务栏入口，防止留下用户无法唤回的后台窗口。
            self.window.showMinimized()
            return
        self.tray.show()
        self.window.hide()
        if not self._notified:
            self._notified = True
            self.tray.showMessage("Calc 已在后台运行", "右键托盘图标可打开或退出 Calc。")

    def restore(self):
        if self._closed:
            return
        if self.window.isMinimized():
            self.window.showNormal()
        else:
            self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def request_exit(self):
        if not self._closed:
            # 先恢复窗口：退出确认/保存失败可见；取消退出后窗口继续可用。
            self.restore()
            self.window.close()

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore()

    def close(self):
        self._closed = True
        self.tray.hide()
        self.menu.close()
