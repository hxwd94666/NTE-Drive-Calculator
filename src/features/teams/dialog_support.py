# 队伍相关弹窗复用明确动作键盘规则和可本地化折叠状态，不拥有业务任务或窗口收尾。
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import QAbstractButton, QDialog, QDialogButtonBox, QGridLayout, QWidget

from src.features.teams.widgets import Disclosure
from src.ui.common.text_catalog import resolve_text_catalog, set_button_text


class ExplicitActionDialog(QDialog):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            focus = self.focusWidget()
            if isinstance(focus, QAbstractButton):
                focus.click()
            event.accept()
            return
        super().keyPressEvent(event)


class LocalizedDisclosure(Disclosure):
    def __init__(self, title, content, *, catalog=None, parent=None):
        self._text = resolve_text_catalog(catalog).render
        super().__init__(title, content, parent=parent)
        set_button_text(self.toggle, title)

    def _expanded(self, expanded):
        super()._expanded(expanded)
        self.toggle.setAccessibleDescription(self._text('teams.ui.expanded' if expanded else 'teams.ui.collapsed'))


class CompactFooter(QWidget):
    """底栏独立于内容滚动；窄窗只把状态换到上一行，动作仍靠右。"""
    def __init__(self, *, buttons, leading=None, status=None, parent=None):
        super().__init__(parent)
        self.buttons, self.leading, self.status = buttons, leading, status
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self._wide = None
        self._reflow(True)
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.timeout.connect(self._configure_focus)
        buttons.installEventFilter(self)

    def _configure_focus(self):
        # 等标准按钮盒完成布局，再移除其初始取消代理，按真实动作顺序进入按钮。
        self.buttons.setFocusProxy(None)
        roles = {QDialogButtonBox.AcceptRole: 0, QDialogButtonBox.ActionRole: 1, QDialogButtonBox.RejectRole: 2}
        actions = ([self.leading] if self.leading is not None else []) + sorted(
            self.buttons.buttons(), key=lambda button: roles.get(self.buttons.buttonRole(button), 1))
        for first, second in zip(actions, actions[1:]):
            QWidget.setTabOrder(first, second)

    def eventFilter(self, watched, event):
        if watched is self.buttons and event.type() in (QEvent.Show, QEvent.LayoutRequest):
            self._focus_timer.start(0)
        return super().eventFilter(watched, event)

    def _reflow(self, wide):
        if wide == self._wide:
            return
        self._wide = wide
        while self.grid.count():
            self.grid.takeAt(0)
        self.grid.setColumnStretch(0, 1)
        row = 0 if wide or self.status is None else 1
        if self.status is not None:
            self.grid.addWidget(self.status, 0, 0, 1, 1 if wide else 3)
        if self.leading is not None:
            self.grid.addWidget(self.leading, row, 1)
        self.grid.addWidget(self.buttons, row, 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow(self.width() >= 700)
