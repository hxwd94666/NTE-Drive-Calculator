# 在指定页面过滤没有可见内容的悬浮提示，保留子控件及父容器的有效提示。
import re

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QStyle, QStyleOptionComboBox, QToolTip, QWidget

from src.ui.common.text_catalog import plain_tooltip


class _ComboCaptionTooltip(QObject):
    """只补全当前选项的省略文字，不把下拉框已显示内容再解释一遍。"""

    def refresh(self, *_args):
        combo = self.parent()
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        field = combo.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, combo)
        text = combo.currentText()
        clipped = combo.fontMetrics().horizontalAdvance(text) > max(0, field.width())
        combo.setToolTip(plain_tooltip(text) if clipped else '')

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show, QEvent.FontChange, QEvent.StyleChange):
            self.refresh()
        return False


def bind_elided_combo_tooltip(combo):
    """只用于原本重复 currentText 的下拉框；有独立帮助或禁用原因的控件不接入。"""
    guard = getattr(combo, '_caption_tooltip_guard', None)
    if guard is None:
        guard = _ComboCaptionTooltip(combo)
        combo._caption_tooltip_guard = guard
        combo.installEventFilter(guard)
        combo.currentTextChanged.connect(guard.refresh)
    guard.refresh()


def tooltip_has_information(text: str) -> bool:
    if not text.strip():
        return False
    # 普通说明中的小于号不是 HTML；只解析 Qt 已知的富文本外壳。
    if re.match(r"\s*<(?:!doctype\s+html|qt|html|body|p|div|span|table|br|b|i|font)\b", text, re.I):
        document = QTextDocument()
        document.setHtml(text)
        text = document.toPlainText()
    return bool(text.strip(" \t\r\n\u00a0\u200b\ufeff"))


class _EmptyTooltipGuard(QObject):
    def watch(self, widget):
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.ChildAdded, QEvent.ChildPolished):
            child = event.child()
            if isinstance(child, QWidget):
                self.watch(child)
            return False
        if event.type() != QEvent.ToolTip or not isinstance(watched, QWidget):
            return False
        root = self.parent()
        if watched is not root and not root.isAncestorOf(watched):
            return False
        # QWidget 会向父容器寻找提示；首个非空属性若只有 HTML 外壳，同样没有信息。
        target = watched
        while target is not None:
            tooltip = target.toolTip()
            if tooltip:
                if tooltip_has_information(tooltip):
                    return False
                break
            if target is root:
                break
            target = target.parentWidget()
        QToolTip.hideText()
        event.accept()
        return True


def suppress_empty_tooltips(page: QWidget) -> None:
    """只观察本页面的提示事件；迟建子控件自动纳入，页面销毁时解除过滤。"""
    if not hasattr(page, "_empty_tooltip_guard"):
        guard = _EmptyTooltipGuard(page)
        # 只装在本页控件树，不让普通绘制和输入事件经过应用级 Python 过滤器。
        guard.watch(page)
        page._empty_tooltip_guard = guard
