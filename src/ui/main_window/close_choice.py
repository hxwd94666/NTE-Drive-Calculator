# 将窗口关闭意图与业务退出分开，先明确后台保留或退出，默认取消且不提前停止任务。
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.ui.common.text_catalog import TextActionButton, plain_label, resolve_text_catalog


class CloseWindowDialog(QDialog):
    def __init__(self, owner, *, tray_available, text_catalog=None):
        super().__init__(owner)
        self.catalog = resolve_text_catalog(text_catalog)
        text = self.catalog.render
        self.action = 'cancel'
        self.setObjectName('closeWindowChoice')
        self.setWindowTitle(text('ui.main_window.choose_close_action.box'))
        self.setWindowFlag(Qt.WindowCloseButtonHint, True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setWindowModality(Qt.WindowModal)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)
        heading = plain_label(text('app.close.heading'), self)
        heading.setWordWrap(True)
        heading.setStyleSheet(themed_style('color:#f0f6fc;font-size:16px;font-weight:700;'))
        root.addWidget(heading)
        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget(scroll)
        choices = QVBoxLayout(content)
        choices.setContentsMargins(0, 0, 0, 0)
        choices.setSpacing(10)
        self.action_buttons = {}
        for action, title, description, caption in (
            ('minimize', 'app.close.background_title',
             'app.close.background_tray' if tray_available else 'app.close.background_taskbar',
             'app.close.minimize'),
            ('exit', 'app.close.exit_title', 'app.close.exit_description', 'app.close.exit')):
            card = QFrame(content)
            card.setObjectName('closeChoiceCard')
            card.setStyleSheet(themed_style(
                'QFrame#closeChoiceCard{background:#161b22;border:1px solid #30363d;border-radius:9px;}'))
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 12, 14, 12)
            row.setSpacing(12)
            copy = QVBoxLayout()
            copy.setSpacing(5)
            label = plain_label(text(title), card)
            label.setWordWrap(True)
            label.setStyleSheet(themed_style('color:#f0f6fc;font-size:14px;font-weight:700;'))
            details = plain_label(text(description), card)
            details.setWordWrap(True)
            details.setStyleSheet(themed_style('color:#8b949e;font-size:12px;'))
            copy.addWidget(label)
            copy.addWidget(details)
            row.addLayout(copy, 1)
            button = TextActionButton(text(caption), card)
            button.setObjectName('closeChoice' + action.title())
            button.setMinimumHeight(36)
            button.setMaximumWidth(160)
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setAccessibleDescription(text(description))
            button.setToolTip('')
            if action == 'minimize':
                button.setStyleSheet(themed_style(
                    'background:#1f6feb33;color:#58a6ff;border:1px solid #58a6ff;border-radius:6px;padding:6px 12px;'))
            else:
                button.setStyleSheet(themed_style(
                    'background:#f8514918;color:#f85149;border:1px solid #f8514980;border-radius:6px;padding:6px 12px;'))
            button.clicked.connect(lambda _checked=False, key=action: self._choose(key))
            self.action_buttons[action] = button
            row.addWidget(button)
            choices.addWidget(card)
        choices.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        self._scroll, self._choices_layout = scroll, choices
        self._initial_geometry_fitted = False
        # 取消只保留标题栏关闭入口；初始焦点留在对话框，避免回车默认选择退出。
        QWidget.setTabOrder(self.action_buttons['minimize'], self.action_buttons['exit'])
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus(Qt.OtherFocusReason)
        # 先用目标宽度计算说明的换行高度，再一次限位；长补丁不把退出动作推到视口之外。
        self.ensurePolished()
        target = fit_dialog_to_available_screen(self, QSize(480, 240))
        margins = root.contentsMargins()
        width = max(1, target.width() - margins.left() - margins.right())
        # 隐藏窗口的 viewport 尚可能是默认宽度；按正式目标宽度求换行高度，避免虚增大片空白。
        required = choices.totalHeightForWidth(width)
        heading_height = heading.heightForWidth(width) if heading.hasHeightForWidth() else heading.sizeHint().height()
        height = max(240, required + heading_height + margins.top() + margins.bottom() + root.spacing() + 4)
        fit_dialog_to_available_screen(self, QSize(target.width(), height))

    def showEvent(self, event):
        super().showEvent(event)
        if self._initial_geometry_fitted:
            return
        self._initial_geometry_fitted = True
        # 全局主题在 Show 前才安装；按实际视口重新核对一次换行高度，不靠隐藏滚动条截掉文字。
        self.layout().activate()
        self._choices_layout.activate()
        viewport = self._scroll.viewport()
        required = self._choices_layout.totalHeightForWidth(viewport.width())
        height = max(self.height(), self.height() - viewport.height() + required + 12)
        fit_dialog_to_available_screen(self, QSize(self.width(), height))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # 仅主动聚焦到明确动作后回车才执行；焦点在窗口时保持打开。
            for action, button in self.action_buttons.items():
                if self.focusWidget() is button:
                    self._choose(action)
                    break
            event.accept()
            return
        super().keyPressEvent(event)

    def _choose(self, action):
        self.action = action
        self.accept()


def choose_close_action(window, tray_available):
    box = CloseWindowDialog(window, tray_available=tray_available,
                            text_catalog=getattr(window, 'text_catalog', None))
    try:
        return box.action if box.exec() == QDialog.Accepted else 'cancel'
    finally:
        box.close()
        box.deleteLater()
