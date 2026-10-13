# 提供通用帮助弹窗工具。
"""通用帮助弹窗：只展示说明，不启动功能。"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from src.app.theme import current_style_sheet


def show_help(parent: QWidget | None, title: str, text: str) -> None:
    # Controller 是 QObject；只接收 QWidget 父级，页面仍应传入实际按钮。
    dialog_parent = parent if isinstance(parent, QWidget) else None
    dlg = QDialog(dialog_parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumSize(380, 220)
    dlg.setStyleSheet(current_style_sheet())
    layout = QVBoxLayout(dlg)
    layout.setSpacing(12)
    label = QLabel(text)
    label.setStyleSheet("font-size:13px;line-height:1.6;padding:8px")
    label.setWordWrap(True)
    layout.addWidget(label)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)
    dlg.exec()
