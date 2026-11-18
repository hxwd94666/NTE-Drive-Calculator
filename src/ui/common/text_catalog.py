# 界面显式消费冻结文本目录；独立窗口回退发行中文，变量和补丁文案均按纯文本展示。
from __future__ import annotations

from html import escape
from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QWidget

from src.domain.localization.catalog import TextCatalog
from src.integrations.preferences.locale_files import bundled_text_catalog
from src.ui.common.display_projection import project


def resolve_text_catalog(catalog: TextCatalog | None) -> TextCatalog:
    return catalog if catalog is not None else bundled_text_catalog()


def plain_label(text="", parent=None) -> QLabel:
    # 保留 QLabel(parent) 调用的父树归属；文本不启用 Qt 自动富文本。
    if isinstance(text, QWidget) and parent is None:
        parent, text = text, ""
    label = QLabel(parent)
    label.setTextFormat(Qt.PlainText)
    label.setText(text)
    return label


def plain_tooltip(text) -> str:
    # 富文本外壳会让整句匹配失效，因此在转义前先做显示投影。
    value = project(str(text))
    # 空的富文本外壳仍会让 Qt 弹出边框；无内容时必须真正清除提示属性。
    return "<qt>" + escape(value).replace("\n", "<br>") + "</qt>" if value.strip() else ""


def rich_label(markup, parent=None) -> QLabel:
    """只消费代码控制的标记；调用方须转义补丁文案、URL 和其他变量。"""
    label = QLabel(parent)
    label.setTextFormat(Qt.RichText)
    label.setText(markup)
    return label


def add_plain_form_row(form, text, field):
    """文本补丁不变成富文本，并保留表单标签的键盘焦点关联。"""
    label = plain_label(text)
    if isinstance(field, QWidget):
        label.setBuddy(field)
    form.addRow(label, field)


class _ElisionTooltip:
    """自动提示只补充被省略的文字；显式帮助、禁用原因及清空意图均由调用方持有。"""

    def setToolTip(self, text):
        self._explicit_text_tooltip = str(text)
        super().setToolTip(text)

    def _update_text_tooltip(self, full, shown):
        tooltip = getattr(self, '_explicit_text_tooltip', None)
        if tooltip is None:
            tooltip = plain_tooltip(full) if shown != full else ''
        super().setToolTip(tooltip)

    def _set_full_text(self, text):
        value = project(str(text))
        # 文案换成另一条状态时不沿用旧原因；同值重绘和窗口变化保留已有帮助。
        # 显式空字符串表示调用方不要提示，这个意图仍需跨文案更新保留。
        if value != getattr(self, '_full_text', None) and getattr(self, '_explicit_text_tooltip', None):
            self._explicit_text_tooltip = None
        self._full_text = value
        self.setAccessibleName(value)


class ElidedTextLabel(_ElisionTooltip, QLabel):
    """紧凑工具栏保持单行，长译文的完整内容由可访问名称及提示保留。"""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setText(text)

    def setText(self, text):
        self._set_full_text(text)
        super().setText(self._full_text)
        if self.isVisible():
            self._elide()
        else:
            self._update_text_tooltip(self._full_text, self._full_text)
        self.updateGeometry()

    def _elide(self):
        # 样式边框、内边距及 QLabel 自动缩进也占宽度；否则带框提示即使有空间仍会截字。
        shown = self.fontMetrics().elidedText(self._full_text, Qt.ElideRight,
                                            max(0, self.width() - self._text_insets()))
        super().setText(shown)
        self._update_text_tooltip(self._full_text, shown)

    def _text_insets(self):
        return max(0, super().sizeHint().width() - self.fontMetrics().horizontalAdvance(super().text()))

    def minimumSizeHint(self):
        size = self.sizeHint()
        return QSize(min(60, size.width()), size.height())

    def sizeHint(self):
        # 异步回填时旧宽度可能为零；尺寸提示必须使用完整文字，避免先省略为空后永久丢失位置。
        return QSize(self.fontMetrics().horizontalAdvance(getattr(self, "_full_text", "")) + self._text_insets(),
                     super().sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange) and hasattr(self, '_full_text') and self.isVisible():
            self._elide()


class _ButtonTextFilter(QObject):
    def __init__(self, button, text):
        super().__init__(button)
        self.text = project(str(text))
        self._automatic_tooltip = ''

    def refresh(self, button):
        shown = button.fontMetrics().elidedText(self.text, Qt.ElideRight, max(0, button.width() - 30))
        button.setText(shown)
        # 标准 Qt 按钮无法拦截调用方的 setter；只有仍由本过滤器拥有的默认提示才更新。
        if button.toolTip() in ('', self._automatic_tooltip):
            self._automatic_tooltip = plain_tooltip(self.text) if shown != self.text else ''
            button.setToolTip(self._automatic_tooltip)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show, QEvent.FontChange, QEvent.StyleChange):
            self.refresh(watched)
        return super().eventFilter(watched, event)


def set_button_text(button, text):
    """标准按钮组也保留标准动作角色，仅为长译文提供省略与完整可访问名称。"""
    button.setProperty('_nte_explicit_text', True)
    if isinstance(button, TextActionButton):
        button.setText(text)
        return
    button.setText(text)
    button.setAccessibleName(text)
    button.setMinimumWidth(min(160, button.fontMetrics().horizontalAdvance(text) + 34))
    button.setMaximumWidth(260)
    handler = getattr(button, "_catalog_text_filter", None)
    if handler is None:
        handler = _ButtonTextFilter(button, text)
        button.installEventFilter(handler)
        button._catalog_text_filter = handler
    else:
        handler.text = str(text)
    handler.refresh(button)


class TextActionButton(_ElisionTooltip, QPushButton):
    """长译文省略展示并保留完整可访问名称和提示，不让按钮挤出可用屏幕。"""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._full_text = project(str(text))
        self.setAccessibleName(self._full_text)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._refresh()

    def _refresh(self):
        shown = self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, max(0, self.width() - 30))
        super().setText(shown)
        self._update_text_tooltip(self._full_text, shown)

    def setText(self, text):
        self._set_full_text(text)
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

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange) and hasattr(self, '_full_text'):
            self._refresh()
