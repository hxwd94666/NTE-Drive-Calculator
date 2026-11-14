# 为优先级头像和角色名提供点击移除与长按拖拽的统一入口。
from PySide6.QtCore import QMimeData, QTimer, Qt
from PySide6.QtGui import QDrag, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton


class PriorityRoleButton(QPushButton):
    def __init__(self, selector, role, index, *, avatar):
        super().__init__()
        self.selector, self.role, self.index = selector, role, index
        self._drag_start_pos = None
        self._dragging = False
        self.setAccessibleName(role)
        self.setAcceptDrops(True)
        self.setCursor(Qt.OpenHandCursor)
        body = QHBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(5)
        avatar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        body.addWidget(avatar)
        name = QLabel(role, self)
        name.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        name.setStyleSheet("background:transparent;border:none;padding:0;font-weight:700;font-size:13px")
        body.addWidget(name, 1)
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._begin_drag)
        self.clicked.connect(lambda _checked=False: selector._toggle(role))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self._drag_start_pos = event.position().toPoint()
            self._hold_timer.start(QApplication.startDragTime())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton and self._drag_start_pos is not None
                and (event.position().toPoint() - self._drag_start_pos).manhattanLength()
                >= QApplication.startDragDistance()):
            self._begin_drag()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._hold_timer.stop()
        self._drag_start_pos = None
        if self._dragging:
            self._dragging = False
            self.setDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _begin_drag(self):
        if self._drag_start_pos is None or self._dragging or not self.isDown():
            return
        self._hold_timer.stop()
        self._dragging = True
        self.setDown(False)
        source_widget = self.parentWidget() or self
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(self.index))
        drag.setMimeData(mime)
        drag.setPixmap(self._make_drag_pixmap(source_widget))
        drag.setHotSpot(self.mapTo(source_widget, self._drag_start_pos))
        self._drag_start_pos = None
        drag.exec(Qt.MoveAction)

    def _make_drag_pixmap(self, source_widget):
        raw = source_widget.grab()
        if raw.isNull():
            return raw
        pixmap = QPixmap(raw.size())
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setOpacity(0.72)
        painter.drawPixmap(0, 0, raw)
        painter.end()
        return pixmap

    def _accepts_drop(self, event):
        source = event.source()
        return (isinstance(source, PriorityRoleButton) and source.selector is self.selector
                and event.mimeData().hasText())

    def dragEnterEvent(self, event):
        if self._accepts_drop(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self._accepts_drop(event):
            return
        try:
            source_index = int(event.mimeData().text())
        except ValueError:
            return
        self.selector._drop_selected_on(source_index, self.index)
        event.acceptProposedAction()
