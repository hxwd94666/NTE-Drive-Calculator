# 提供通用帮助弹窗工具。
"""Shared dialog helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from src.app.theme import current_style_sheet
from src.i18n import tr


def show_help(parent: QWidget | None, title: str, text: str) -> None:
    # Feature controllers are QObjects rather than widgets.  Keep this shared
    # boundary defensive so an accidental controller caller cannot crash Qt's
    # strict parent overload; page builders should still pass their button.
    dialog_parent = parent if isinstance(parent, QWidget) else None
    dlg = QDialog(dialog_parent)
    # Help copy is defined as module-level constants, which are imported before
    # set_language() runs. Translate here, at call time, instead.
    dlg.setWindowTitle(tr(title))
    dlg.setMinimumSize(380, 220)
    dlg.setStyleSheet(current_style_sheet())
    layout = QVBoxLayout(dlg)
    layout.setSpacing(12)
    label = QLabel(tr(text))
    label.setStyleSheet("font-size:13px;line-height:1.6;padding:8px")
    label.setWordWrap(True)
    layout.addWidget(label)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)
    dlg.exec()
