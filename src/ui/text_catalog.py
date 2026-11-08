# 界面显式消费冻结文本目录；独立窗口回退发行中文，变量和补丁文案均按纯文本展示。
from __future__ import annotations

from html import escape
from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy

from src.domain.localization.catalog import TextCatalog
from src.integrations.preferences.locale_files import bundled_text_catalog


def resolve_text_catalog(catalog: TextCatalog | None) -> TextCatalog:
    return catalog if catalog is not None else bundled_text_catalog()


def plain_label(text="", parent=None) -> QLabel:
    label = QLabel(parent)
    label.setTextFormat(Qt.PlainText)
    label.setText(text)
    return label


def plain_tooltip(text) -> str:
    return "<qt>" + escape(str(text)).replace("\n", "<br>") + "</qt>"


class ElidedTextLabel(QLabel):
    """紧凑工具栏保持单行，长译文的完整内容由可访问名称及提示保留。"""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setText(text)

    def setText(self, text):
        self._full_text = str(text)
        self.setAccessibleName(self._full_text)
        self.setToolTip(plain_tooltip(self._full_text))
        super().setText(self._full_text)
        if self.isVisible():
            self._elide()
        self.updateGeometry()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, self.width()))

    def minimumSizeHint(self):
        size = self.sizeHint()
        return QSize(min(60, size.width()), size.height())

    def sizeHint(self):
        # 异步回填时旧宽度可能为零；尺寸提示必须使用完整文字，避免先省略为空后永久丢失位置。
        return QSize(self.fontMetrics().horizontalAdvance(getattr(self, "_full_text", "")), super().sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class _ButtonTextFilter(QObject):
    def __init__(self, button, text):
        super().__init__(button)
        self.text = str(text)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            watched.setText(
                watched.fontMetrics().elidedText(self.text, Qt.ElideRight, max(0, event.size().width() - 30))
            )
        return super().eventFilter(watched, event)


def set_button_text(button, text):
    """标准按钮组也保留标准动作角色，仅为长译文提供省略与完整可访问名称。"""
    if isinstance(button, TextActionButton):
        button.setText(text)
        return
    button.setText(text)
    button.setAccessibleName(text)
    button.setToolTip(plain_tooltip(text))
    button.setMinimumWidth(min(160, button.fontMetrics().horizontalAdvance(text) + 34))
    button.setMaximumWidth(260)
    handler = getattr(button, "_catalog_text_filter", None)
    if handler is None:
        handler = _ButtonTextFilter(button, text)
        button.installEventFilter(handler)
        button._catalog_text_filter = handler
    else:
        handler.text = str(text)


class TextActionButton(QPushButton):
    """长译文省略展示并保留完整可访问名称和提示，不让按钮挤出可用屏幕。"""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._full_text = str(text)
        self.setAccessibleName(self._full_text)
        self.setToolTip(plain_tooltip(self._full_text))
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._refresh()

    def _refresh(self):
        super().setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, max(0, self.width() - 30)))

    def setText(self, text):
        self._full_text = str(text)
        self.setAccessibleName(self._full_text)
        self.setToolTip(plain_tooltip(self._full_text))
        self._refresh()
        self.updateGeometry()

    def sizeHint(self):
        return QSize(min(280, self.fontMetrics().horizontalAdvance(self._full_text) + 38), super().sizeHint().height())

    def minimumSizeHint(self):
        size = self.sizeHint()
        return QSize(min(90, size.width()), size.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()
