# 持有本窗口托盘入口，隐藏不释放业务 owner，明确退出仍走主窗口收尾。
from src.ui.common.text_catalog import resolve_text_catalog
from src.ui.main_window.close_choice import choose_close_action
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMenu, QStyle, QSystemTrayIcon



class TrayController(QObject):
    def __init__(self, window, *, available=None, choose_close=None, text_catalog=None):
        super().__init__(window)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.window = window
        self._available = available or QSystemTrayIcon.isSystemTrayAvailable
        self._choose_close = choose_close or choose_close_action
        self._choosing_close = False
        self._notified = False
        self._closed = False
        self.menu = QMenu(window)
        self.menu.addAction(resolve_text_catalog(text_catalog).render('ui.main_window.init.menu'), self.restore)
        self.menu.addSeparator()
        self.menu.addAction(resolve_text_catalog(text_catalog).render('ui.main_window.init.menu_2'), self.request_exit)
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
            self.tray.showMessage(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.main_window.hide_window.tray'), self.text_catalog.render('app.close.tray_hint'))

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
