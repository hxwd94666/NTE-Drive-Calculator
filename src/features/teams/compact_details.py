# 按可见内容限制说明区和首次窗口尺寸；展开/分页不重置用户调整，不接管业务任务。
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QPlainTextEdit, QSizePolicy, QTabWidget

from src.app.window_geometry import fit_dialog_to_available_screen


class CompactReadOnlyText(QPlainTextEdit):
    """短说明只占内容高度，长说明在本区滚动；高度随字体与换行宽度更新。"""
    def __init__(self, parent=None, *, maximum_lines=9):
        super().__init__(parent)
        self._maximum_lines = maximum_lines
        self.setReadOnly(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.textChanged.connect(self._fit_text)
        self._fit_text()

    def _fit_text(self):
        metrics = QFontMetrics(self.font())
        width = max(120, self.viewport().width())
        height = metrics.boundingRect(QRect(0, 0, width, 1_000_000), Qt.TextWordWrap,
                                      self.toPlainText() or ' ').height()
        height = max(metrics.lineSpacing() * 2, min(metrics.lineSpacing() * self._maximum_lines, height))
        self.setFixedHeight(height + 2 * self.frameWidth() + int(2 * self.document().documentMargin()) + 4)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._fit_text()

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, '_maximum_lines') and event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._fit_text()


class CurrentContentTabs(QTabWidget):
    """隐藏页不为当前页预留高度；切换只重新布局，不强改顶层窗口尺寸。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.currentChanged.connect(lambda _index: self.updateGeometry())

    def _body_height(self, width):
        page = self.currentWidget()
        if page is None:
            return 0
        layout = page.layout()
        if layout is not None:
            wanted = layout.totalHeightForWidth(max(120, width - 8))
            height = max(layout.minimumSize().height(), wanted)
        else:
            height = page.sizeHint().height()
        return max(page.minimumHeight(), min(page.maximumHeight(), height))

    def sizeHint(self):
        return QSize(280, self.heightForWidth(max(280, self.width())))

    def minimumSizeHint(self):
        return QSize(240, self.sizeHint().height())

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.tabBar().sizeHint().height() + self._body_height(width) + 6


class InitialContentFit(QObject):
    """首次读取完成可校正一次初始大小；用户已调整或后续展开时不再自动缩放。"""
    def __init__(self, dialog, *, body, footer, width):
        super().__init__(dialog)
        self.dialog, self.body, self.footer, self.width = dialog, body, footer, width
        self._last_auto_size, self._ready, self._settled = None, False, False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fit)

    def shown(self):
        if self._last_auto_size is None:
            self._fit()
        self._timer.start(0)

    def ready(self):
        if not self._settled:
            self._ready = True
            self._timer.start(0)

    def _measure(self, width):
        layout = self.body.layout()
        outer = self.dialog.layout()
        margins = outer.contentsMargins()
        inner = max(120, width - margins.left() - margins.right() - 8)
        height = max(layout.minimumSize().height(), layout.totalHeightForWidth(inner))
        return QSize(width, height + self.footer.sizeHint().height() + outer.spacing()
                     + margins.top() + margins.bottom() + 8)

    def _fit(self):
        if not self.dialog.isVisible():
            return
        if self._settled or (self._last_auto_size is not None and self.dialog.size() != self._last_auto_size):
            self._settled = True
            fit_dialog_to_available_screen(self.dialog)
            return
        preferred = self._measure(self.width)
        fitted = fit_dialog_to_available_screen(self.dialog, preferred)
        if fitted.width() != preferred.width():
            fit_dialog_to_available_screen(self.dialog, self._measure(fitted.width()))
        self._last_auto_size = QSize(self.dialog.size())
        self._settled = self._ready


def reveal_expanded_details(scroll, section):
    """展开后只滚动本窗内容，让诊断开头可见；定时器随折叠区物理销毁。"""
    timer = QTimer(section)
    timer.setSingleShot(True)

    def reveal():
        if section.content.isVisible() and scroll.widget() is not None:
            point = section.content.mapTo(scroll.widget(), QPoint(0, 0))
            scroll.ensureVisible(0, point.y() + 2 * section.fontMetrics().lineSpacing(), 0, 12)

    timer.timeout.connect(reveal)
    section.toggle.toggled.connect(lambda expanded: timer.start(0) if expanded else timer.stop())
