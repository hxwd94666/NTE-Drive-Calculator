# 提供只接受纯文本的输入控件。
"""粘贴只消费纯文本；图片与富文本格式不进入编辑内容。"""

from __future__ import annotations

from PySide6.QtWidgets import QTextEdit


class PlainTextOnlyTextEdit(QTextEdit):
    def insertFromMimeData(self, source):
        if source.hasText():
            self.insertPlainText(source.text())
