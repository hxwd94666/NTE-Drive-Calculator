# 用最多五列的虚拟化图片卡展示历史材料，保留冻结数量与键盘可访问性。
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QListView, QSizePolicy, QStyle, QStyledItemDelegate, QWidget,
)

from src.app.theme import theme_color

IconLookup = Callable[[str], str | Path | None]
_AMOUNTS = (("总需求", "required"), ("有效抵扣", "allocated_equivalent"), ("仍需", "remaining"))


class HistoryMaterialModel(QAbstractListModel):
    """只呈现历史事实；图片从注入的受管目录按正式 ID 解析，不进入历史正文。"""

    def __init__(self, icon_lookup: IconLookup, parent: QWidget) -> None:
        super().__init__(parent)
        self._icon_lookup = icon_lookup
        self._rows: tuple[dict, ...] = ()
        self._icons: OrderedDict[str, QIcon] = OrderedDict()
        self._maximum_amount = 0

    def set_materials(self, materials: list[dict]) -> None:
        self.beginResetModel()
        self._rows = tuple(dict(item) for item in materials)
        self._maximum_amount = max((item["required"] for item in self._rows), default=0)
        self._icons.clear()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802 - Qt override
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        item = self._rows[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole,
                    Qt.ItemDataRole.AccessibleTextRole):
            return item["name"] + "；" + "；".join(
                f"{title} {item[field]:,}" for title, field in _AMOUNTS
            )
        if role == Qt.ItemDataRole.UserRole:
            return dict(item)
        if role == Qt.ItemDataRole.DecorationRole:
            item_id = item["item_id"]
            if item_id not in self._icons:
                try:
                    path = self._icon_lookup(item_id)
                except OSError:
                    path = None
                self._icons[item_id] = QIcon(str(path)) if path else QIcon()
                if len(self._icons) > 128:
                    self._icons.popitem(last=False)
            self._icons.move_to_end(item_id)
            return self._icons[item_id]
        return None

    def flags(self, index):
        return (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if index.isValid() else Qt.ItemFlag.NoItemFlags)

    def maximum_amount(self) -> int:
        return self._maximum_amount


class _MaterialDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):  # noqa: N802 - Qt override
        size = self.parent().gridSize()
        return size if size.isValid() else QSize(180, 146)

    def paint(self, painter, option, index) -> None:
        item = index.data(Qt.ItemDataRole.UserRole)
        if item is None:
            return
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(4, 4, -4, -4)
        active = option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)
        painter.setPen(QColor(theme_color("#58a6ff" if active else "#30363d")))
        painter.setBrush(QColor(theme_color("#161b22" if active else "#0d1117")))
        painter.drawRoundedRect(rect, 8, 8)
        icon_rect = QRect(rect.left() + 10, rect.top() + 10, 48, 48)
        painter.setBrush(QColor(theme_color("#161b22")))
        painter.drawRoundedRect(icon_rect, 6, 6)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon is not None and not icon.isNull():
            icon.paint(painter, icon_rect.adjusted(2, 2, -2, -2))
        else:
            painter.setPen(QColor(theme_color("#8b949e")))
            painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, (item["name"].strip() or "材")[:1])
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme_color("#f0f6fc")))
        name_rect = QRect(icon_rect.right() + 10, icon_rect.top(), rect.width() - 78, 48)
        # Full names and amounts remain accessible in the model and tooltip.
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(font).elidedText(item["name"], Qt.TextElideMode.ElideRight, name_rect.width()))
        line_height = max(22, option.fontMetrics.lineSpacing() + 2)
        for number, (title, field) in enumerate(_AMOUNTS):
            font.setBold(number == 2)
            painter.setFont(font)
            row = QRect(rect.left() + 10, icon_rect.bottom() + 8 + number * line_height,
                        rect.width() - 20, line_height)
            painter.setPen(QColor(theme_color("#8b949e")))
            painter.drawText(row, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
            painter.setPen(QColor(theme_color("#f0f6fc")))
            painter.drawText(row, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{item[field]:,}")
        painter.restore()


class HistoryMaterialGrid(QListView):
    """宽屏最多五列，全部材料按行铺开，由历史预览外层统一滚动。"""

    focus_item_requested = Signal(QRect)

    def __init__(self, parent: QWidget | None = None, *, icon_lookup: IconLookup | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cultivationHistoryMaterialTotals")
        self.setAccessibleName("当时材料合计（只读）")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        # All rows are exposed: uniform geometry must be ready for keyboard End/scroll mapping.
        self.setLayoutMode(QListView.LayoutMode.SinglePass)
        self.setUniformItemSizes(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self._materials = HistoryMaterialModel(icon_lookup or (lambda _item_id: None), self)
        self.setModel(self._materials)
        self.setItemDelegate(_MaterialDelegate(self))
        self._columns = 1
        self._refresh_geometry()

    def set_materials(self, materials: list[dict]) -> None:
        self._materials.set_materials(materials)
        self._refresh_geometry()
        self.verticalScrollBar().setValue(0)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(180, self.height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refresh_geometry()

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt override
        event.ignore()  # Let the owning history preview scroll, not an inner view.

    def currentChanged(self, current, previous) -> None:  # noqa: N802 - Qt override
        super().currentChanged(current, previous)
        if current.isValid():
            QTimer.singleShot(0, self, self._reveal_current)

    def _reveal_current(self) -> None:
        if self.hasFocus() and self.currentIndex().isValid():
            self.focus_item_requested.emit(self.visualRect(self.currentIndex()))

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and hasattr(self, "_materials"):
            self._refresh_geometry()

    def _refresh_geometry(self) -> None:
        available = max(1, self.contentsRect().width())
        bold = QFont(self.font())
        bold.setBold(True)
        metrics = QFontMetrics(bold)
        minimum = max(180, metrics.horizontalAdvance("有效抵扣")
                      + metrics.horizontalAdvance(f"{self._materials.maximum_amount():,}") + 40)
        self._columns = min(5, max(1, available // minimum))
        line_height = max(22, self.fontMetrics().lineSpacing() + 2)
        cell = QSize(max(1, available // self._columns), 80 + line_height * 3)
        if self.gridSize() != cell:
            self.setGridSize(cell)
        rows = (self._materials.rowCount() + self._columns - 1) // self._columns
        height = max(1, rows) * cell.height()
        if self.height() != height:
            self.setFixedHeight(height)
