# 提供只接受纯文本的输入控件。
"""Text edit variants used by feature pages."""

from __future__ import annotations

from PySide6.QtWidgets import QTextEdit


class PlainTextOnlyTextEdit(QTextEdit):
    def insertFromMimeData(self, source):
        if source.hasText():
            self.insertPlainText(source.text())
