# 队伍成员共用头像按钮，显示名仅用于展示，选择始终绑定正式角色身份。
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QGridLayout, QSizePolicy, QToolButton, QWidget

from src.app.theme import themed_style
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
from src.ui.roles.role_portrait import custom_role_portrait, temporary_role_portrait


def role_button(role=None, *, parent=None, checkable=False, compact=False, dense=False, catalog=None):
    button = QToolButton(parent)
    button._text_catalog = resolve_text_catalog(catalog)
    button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    button.setIconSize(QSize(36, 36))
    button.setMinimumSize(140, 58)
    button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    button.setCheckable(checkable)
    button.setFocusPolicy(Qt.StrongFocus)
    button.setStyleSheet(themed_style(
        "QToolButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;"
        "border-radius:8px;padding:7px;font-size:13px;font-weight:600;}"
        "QToolButton:hover{border-color:#58a6ff;}"
        "QToolButton:checked{background:#1f6feb;color:#ffffff;border:2px solid #58a6ff;}"
        "QToolButton:focus{border:2px solid #58a6ff;}"
        "QToolButton:disabled{color:#6e7681;background:#0d1117;border-color:#30363d;}"
    ))
    if compact:
        # 列表只保留头像与名称，不把每名成员画成一张大卡；编辑选人仍沿用可选卡片。
        button.setMinimumSize(72, 74)
        button.setMaximumWidth(88)
        button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        button.setIconSize(QSize(44, 44))
        button.setStyleSheet(themed_style(
            "QToolButton{background:transparent;color:#c9d1d9;border:1px solid transparent;"
            "border-radius:5px;padding:4px;font-size:12px;font-weight:500;}"
            "QToolButton:hover{background:#161b22;border-color:#30363d;}"
            "QToolButton:focus{border-color:#58a6ff;}"
            "QToolButton:disabled{color:#6e7681;}"))
    if dense:
        # 靠右紧凑布局必须保留列的自然宽度；Ignored 配合 AlignLeft 会把列压成零并导致头像重叠。
        button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        button.setMinimumSize(86, 42)
        button.setIconSize(QSize(28, 28))
        button.setStyleSheet(themed_style(
            "QToolButton{background:transparent;color:#c9d1d9;border:1px solid transparent;"
            "border-radius:5px;padding:4px;font-size:12px;font-weight:500;}"
            "QToolButton:hover{background:#21262d;border-color:#30363d;}"
            "QToolButton:focus{border-color:#58a6ff;}QToolButton:disabled{color:#6e7681;}"))
    set_role(button, role)
    return button


def set_role(button, role):
    if role is None:
        button.setText(button._text_catalog.render("teams.member.empty"))
        button.setIcon(QIcon())
        return
    button.setText(role.name)
    button.setToolTip(plain_tooltip(role.name + ('\n' + role.icon_note if role.icon_note else '')))
    button.setAccessibleName(role.name)
    icon = QIcon(custom_role_portrait(72, button.devicePixelRatioF())) if role.custom else QIcon(role.icon_path)
    if role.icon_note and not role.custom:
        icon = QIcon(temporary_role_portrait(role.icon_path, 36, button.devicePixelRatioF()))
    button.setIcon(icon)


class MemberSlots(QWidget):
    """四个成员位置随可用宽度换行，不把弹窗最小宽度撑出屏幕。"""
    def __init__(self, parent=None, *, compact=False, dense=False, catalog=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        if compact or dense:
            # 成员按钮按内容宽度成组靠左，窗口变宽不再把额外空间摊进头像之间。
            self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)
            self.grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._minimum_column = 80 if compact else 94 if dense else 148
        self.buttons = [role_button(parent=self, compact=compact, dense=dense, catalog=catalog) for _ in range(4)]
        self._columns = 0
        # 编辑弹窗先用两列限高；列表先按完整四人组给出尺寸提示，再随实际宽度换行。
        self.reflow(columns=4 if compact or dense else 2)

    def reflow(self, columns=None):
        columns = columns or max(1, min(4, (self.width() + 8) // self._minimum_column))
        # 四人成组只用 4/2/1 列，避免窄屏出现三人一行、第四人孤立一行。
        if columns == 3:
            columns = 2
        if columns == self._columns:
            return
        self._columns = columns
        while self.grid.count():
            self.grid.takeAt(0)
        for index, button in enumerate(self.buttons):
            self.grid.addWidget(button, index // columns, index % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()
