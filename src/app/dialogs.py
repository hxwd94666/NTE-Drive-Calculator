# 提供通用帮助弹窗工具。
"""Shared dialog helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from src.app.theme import current_style_sheet


def show_help(parent, title, text):
    dlg = QDialog(parent)
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
