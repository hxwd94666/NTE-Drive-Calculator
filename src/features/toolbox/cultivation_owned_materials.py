# 提供养成计算器已有材料录入、原生归档导入和扣减界面。
"""Owned-material inputs shared by the toolbox cultivation result."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import theme_color, themed_style
from src.domain.progression_material_conversion import allocate_owned, lower_tier_ids
from src.features.toolbox.cultivation_controls import disable_numeric_input_method
from src.services.cultivation_planner_service import CultivationMaterial
from src.ui.progression_material_card import ProgressionMaterialCard
from src.ui.progression_material_grid import ProgressionMaterialGrid


IconLookup = Callable[[str], str | Path | None]


class _OwnedQuantitySpinBox(QSpinBox):
    """Accept typed quantities without wheel or arrow-button changes."""

    advance_requested = Signal(int)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.lineEdit().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if watched is self.lineEdit() and event.type() == QEvent.Type.MouseButtonRelease:
            QTimer.singleShot(0, self, self.selectAll)
        return super().eventFilter(watched, event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt override
        event.ignore()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.advance_requested.emit(-1 if event.key() == Qt.Key.Key_Backtab else 1)
            event.accept()
            return
        super().keyPressEvent(event)


class _OwnedMaterialCanvas(QWidget):
    """Paint all material cards and keep one stable editor for the selected card."""

    quantity_changed = Signal(str)
    layout_changed = Signal()

    _CARD_HEIGHT = 150
    _MIN_WIDTH = 132
    _GAP = 8

    def __init__(self, icon_lookup: IconLookup, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("cultivationOwnedMaterialGrid")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_lookup = icon_lookup
        self._materials: tuple[CultivationMaterial, ...] = ()
        self._owned: dict[str, int] = {}
        self._icons: dict[str, QPixmap] = {}
        self._selected_id: str | None = None
        self._updating = False
        self._columns = 1
        self._editor = _OwnedQuantitySpinBox(self)
        self._editor.setObjectName("cultivationOwnedMaterialQuantity")
        self._editor.setRange(0, 99_999_999)
        disable_numeric_input_method(self._editor)
        self._editor.valueChanged.connect(self._quantity_edited)
        self._editor.advance_requested.connect(self._advance_selection)
        self._editor.hide()
        self.setFixedHeight(0)

    @property
    def column_count(self) -> int:
        return self._columns

    @property
    def editor(self) -> QSpinBox:
        return self._editor

    def quantities(self) -> dict[str, int]:
        return dict(self._owned)

    def refresh_quantities(self, values: Mapping[str, int]) -> None:
        self.set_materials(self._materials, values)

    def clear_quantities(self) -> None:
        self.refresh_quantities({})

    def required_quantity(self, item_id: str) -> int | None:
        return next(
            (item.quantity for item in self._materials if item.item_id == item_id),
            None,
        )

    def set_materials(
        self,
        materials: tuple[CultivationMaterial, ...],
        saved_owned: Mapping[str, int] | None = None,
    ) -> None:
        """Replace only model data; result delivery creates no child QWidget."""

        self._updating = True
        try:
            ids = {item.item_id for item in materials}
            if self._selected_id not in ids:
                self._editor.clearFocus()
                self._editor.hide()
                self._selected_id = None
            values = self._owned if saved_owned is None else saved_owned
            self._owned = {item.item_id: values.get(item.item_id, 0) for item in materials}
            self._materials = materials
            if self._selected_id is not None:
                self._editor.blockSignals(True)
                self._editor.setValue(self._owned[self._selected_id])
                self._editor.blockSignals(False)
            for item in materials:
                if item.item_id not in self._icons:
                    self._icons[item.item_id] = QPixmap(
                        str(self._icon_lookup(item.item_id) or "")
                    )
        finally:
            self._updating = False
        self._refresh_geometry()
        self._place_editor()
        self.update()

    def select_material(self, item_id: str) -> bool:
        if item_id not in self._owned:
            return False
        self._selected_id = item_id
        self._editor.blockSignals(True)
        self._editor.setValue(self._owned[item_id])
        self._editor.blockSignals(False)
        self._place_editor()
        self._editor.show()
        self._editor.setFocus()
        self._editor.selectAll()
        self.update()
        return True

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._MIN_WIDTH, self.height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refresh_geometry()
        self._place_editor()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        for index, material in enumerate(self._materials):
            if self._card_rect(index).contains(event.position().toPoint()):
                self.select_material(material.item_id)
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._materials and event.key() in (
            Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space,
        ):
            self.select_material(self._materials[0].item_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        base_font = painter.font()
        for index, material in enumerate(self._materials):
            rect = self._card_rect(index)
            active = material.item_id == self._selected_id
            painter.setPen(QPen(QColor(theme_color(
                "#58a6ff" if active else "#30363d"
            )), 1))
            painter.setBrush(QColor(theme_color("#0d1117")))
            painter.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 8, 8)
            icon_rect = QRect(rect.center().x() - 21, rect.top() + 8, 42, 42)
            painter.setPen(QPen(QColor(theme_color("#30363d")), 1))
            painter.setBrush(QColor(theme_color("#161b22")))
            painter.drawRoundedRect(icon_rect, 6, 6)
            icon = self._icons.get(material.item_id)
            if icon is not None and not icon.isNull():
                painter.drawPixmap(icon_rect, icon, icon.rect())
            else:
                painter.setPen(QColor(theme_color("#8b949e")))
                painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter,
                                 (material.name.strip() or "材")[:1])
            name_rect = QRect(rect.left() + 8, rect.top() + 55, rect.width() - 16, 22)
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(theme_color("#f0f6fc")))
            name = painter.fontMetrics().elidedText(
                material.name, Qt.TextElideMode.ElideRight, name_rect.width()
            )
            painter.drawText(name_rect, Qt.AlignmentFlag.AlignCenter, name)
            painter.setFont(base_font)
            painter.setPen(QColor(theme_color("#58a6ff")))
            painter.drawText(
                QRect(rect.left() + 8, rect.top() + 80, rect.width() - 16, 20),
                Qt.AlignmentFlag.AlignCenter,
                (f"需要 × {max(0, int(material.quantity)):,}"
                 if material.quantity else "可合成材料"),
            )
            painter.setPen(QColor(theme_color("#8b949e")))
            painter.drawText(
                QRect(rect.left() + 8, rect.top() + 111, 42, 28),
                Qt.AlignmentFlag.AlignVCenter,
                "已有",
            )
            if not active:
                field_rect = self._editor_rect(rect)
                painter.setPen(QPen(QColor(theme_color("#30363d")), 1))
                painter.setBrush(QColor(theme_color("#161b22")))
                painter.drawRoundedRect(field_rect, 5, 5)
                painter.setPen(QColor(theme_color("#c9d1d9")))
                painter.drawText(
                    field_rect,
                    Qt.AlignmentFlag.AlignCenter,
                    f"{self._owned.get(material.item_id, 0):,}",
                )
        painter.end()

    def _quantity_edited(self, value: int) -> None:
        if self._updating or self._selected_id is None:
            return
        if self._owned.get(self._selected_id) == value:
            return
        self._owned[self._selected_id] = value
        self.quantity_changed.emit(self._selected_id)
        self.update()

    def _advance_selection(self, step: int) -> None:
        if not self._materials or self._selected_id is None:
            return
        ids = tuple(item.item_id for item in self._materials)
        index = ids.index(self._selected_id)
        self.select_material(ids[(index + step) % len(ids)])

    def _refresh_geometry(self) -> None:
        available = max(self._MIN_WIDTH, self.width())
        columns = min(5, max(1, (available + self._GAP) // (self._MIN_WIDTH + self._GAP)))
        rows = (len(self._materials) + columns - 1) // columns
        height = rows * self._CARD_HEIGHT + max(0, rows - 1) * self._GAP
        if columns != self._columns or height != self.height():
            self._columns = columns
            if height != self.height():
                self.setFixedHeight(height)
            self.layout_changed.emit()

    def _card_rect(self, index: int) -> QRect:
        width = max(self._MIN_WIDTH, (self.width() - (self._columns - 1) * self._GAP) // self._columns)
        column = index % self._columns
        row = index // self._columns
        return QRect(column * (width + self._GAP), row * (self._CARD_HEIGHT + self._GAP),
                     width, self._CARD_HEIGHT)

    @staticmethod
    def _editor_rect(card: QRect) -> QRect:
        return QRect(card.left() + 52, card.top() + 110,
                     max(62, card.width() - 62), 30)

    def _place_editor(self) -> None:
        if self._selected_id is None:
            return
        for index, material in enumerate(self._materials):
            if material.item_id == self._selected_id:
                self._editor.setGeometry(self._editor_rect(self._card_rect(index)))
                return


class CultivationOwnedMaterials(QFrame):
    """Own manual inventory inputs without writing an account or game snapshot."""

    quantities_changed = Signal()
    layout_changed = Signal()
    import_requested = Signal()

    def __init__(self, icon_lookup: IconLookup, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("cultivationOwnedMaterials")
        self.setStyleSheet(themed_style(
            "QFrame#cultivationOwnedMaterials{background:#161b22;"
            "border:1px solid #30363d;border-radius:9px;}"
        ))
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 12)
        root.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("已有材料", self)
        title.setStyleSheet(themed_style(
            "color:#c9d1d9;font-size:14px;font-weight:800"
        ))
        header.addWidget(title)
        header.addWidget(QLabel("点击材料卡填写已有数量，再重新计算", self))
        header.addStretch(1)
        self._import_button = QPushButton("同步材料", self)
        self._import_button.setObjectName("cultivationOwnedImport")
        self._import_button.setEnabled(False)
        self._import_button.setToolTip("读取当前账号最近的原生物品归档；抓包背包暂不提供材料数量。")
        self._import_button.clicked.connect(lambda _checked=False: self.import_requested.emit())
        header.addWidget(self._import_button)
        clear = QPushButton("清空", self)
        clear.setObjectName("cultivationOwnedClear")
        clear.setToolTip("清空本次草稿中的全部已有材料数量，不修改账号物品归档。")
        clear.clicked.connect(lambda _checked=False: self.clear_quantities())
        header.addWidget(clear)
        root.addLayout(header)
        self._import_status = QLabel("原生材料需先完成原生背包同步；未观测材料保持手填值。", self)
        self._import_status.setObjectName("cultivationOwnedImportStatus")
        self._import_status.setWordWrap(True)
        self._import_status.setStyleSheet(themed_style("color:#8b949e"))
        root.addWidget(self._import_status)
        self._placeholder = QLabel("先计算一次目标，随后可填写本次所需材料的已有数量。", self)
        self._placeholder.setObjectName("cultivationOwnedMaterialsPlaceholder")
        self._placeholder.setStyleSheet(themed_style("color:#8b949e"))
        root.addWidget(self._placeholder)
        self._canvas = _OwnedMaterialCanvas(icon_lookup, self)
        self._owned_cache: dict[str, int] = {}
        self._manual_overrides: set[str] = set()
        self._canvas.quantity_changed.connect(self._quantity_changed)
        self._canvas.layout_changed.connect(self.layout_changed)
        root.addWidget(self._canvas)

    def quantities(self) -> dict[str, int]:
        return dict(self._owned_cache)

    def set_import_available(self, available: bool) -> None:
        self._import_button.setEnabled(available)

    def set_import_status(self, message: str) -> None:
        self._import_status.setText(message)
        self.layout_changed.emit()

    def apply_import(self, quantities: Mapping[str, int]) -> int:
        """Overlay observed entries only; manual edits and absent IDs remain unchanged."""

        changed = 0
        for item_id, amount in quantities.items():
            if item_id in self._manual_overrides:
                continue
            if self._owned_cache.get(item_id) != amount:
                self._owned_cache[item_id] = amount
                changed += 1
        if changed:
            self._canvas.refresh_quantities(self._owned_cache)
            self.quantities_changed.emit()
        return changed

    def clear_quantities(self) -> None:
        """Clear only the draft quantities, including currently hidden materials."""

        changed = any(self._owned_cache.values())
        self._owned_cache.clear()
        self._manual_overrides.clear()
        self._canvas.clear_quantities()
        self._owned_cache.update(self._canvas.quantities())
        self.set_import_status("已有材料草稿已清空；账号原生归档保持不变。")
        if changed:
            self.quantities_changed.emit()

    def _quantity_changed(self, item_id: str) -> None:
        self._owned_cache.update(self._canvas.quantities())
        self._manual_overrides.add(item_id)
        self.quantities_changed.emit()

    def select_material(self, item_id: str) -> bool:
        return self._canvas.select_material(item_id)

    def required_quantity(self, item_id: str) -> int | None:
        return self._canvas.required_quantity(item_id)

    @property
    def column_count(self) -> int:
        return self._canvas.column_count

    def set_materials(self, materials: tuple[CultivationMaterial, ...]) -> None:
        self._owned_cache.update(self._canvas.quantities())
        self._canvas.set_materials(materials, self._owned_cache)
        self._owned_cache.update(self._canvas.quantities())
        self._placeholder.setVisible(not materials)
        self.layout_changed.emit()

    def clear_materials(self) -> None:
        self._owned_cache.clear()
        self._manual_overrides.clear()
        self._canvas.set_materials(())
        self._placeholder.setVisible(True)
        self.set_import_status("原生材料需先完成原生背包同步；未观测材料保持手填值。")
        self.layout_changed.emit()


def visible_materials(
    materials: tuple[CultivationMaterial, ...],
    scope: str,
    stamina_item_ids: frozenset[str],
) -> tuple[CultivationMaterial, ...]:
    """Filter presentation and manual input without changing the full plan."""

    if scope == "all":
        return materials
    if scope != "stamina":
        raise ValueError(f"未知材料范围：{scope}")
    return tuple(item for item in materials if item.item_id in stamina_item_ids)


def visible_owned_inputs(
    options: tuple[CultivationMaterial, ...],
    required: tuple[CultivationMaterial, ...],
    scope: str,
    stamina_item_ids: frozenset[str],
) -> tuple[CultivationMaterial, ...]:
    """Expose lower-tier inventory when it can craft a required paid material."""

    if scope == "all":
        return options
    selected = {item.item_id for item in required if item.item_id in stamina_item_ids}
    for item_id in tuple(selected):
        selected.update(lower_tier_ids(item_id))
    return tuple(item for item in options if item.item_id in selected)


def remaining_materials(
    materials: tuple[CultivationMaterial, ...],
    owned_quantities: Mapping[str, int],
) -> tuple[CultivationMaterial, ...]:
    """Subtract owned quantities from merged totals and omit fulfilled rows."""

    allocated = allocate_owned(
        {material.item_id: material.quantity for material in materials},
        dict(owned_quantities),
    )
    remaining: list[CultivationMaterial] = []
    for material in materials:
        quantity = max(0, material.quantity - allocated.get(material.item_id, 0))
        if quantity:
            remaining.append(CultivationMaterial(
                item_id=material.item_id,
                name=material.name,
                quantity=quantity,
                quality=material.quality,
                icon_path=material.icon_path,
            ))
    return tuple(remaining)


def build_material_grid(
    materials: tuple[CultivationMaterial, ...],
    *,
    icon_lookup: IconLookup,
    parent: QWidget,
    minimum_card_width: int = 132,
) -> ProgressionMaterialGrid:
    """Build the common five-column calculator material grid."""

    grid = ProgressionMaterialGrid(
        minimum_card_width=minimum_card_width,
        maximum_columns=5,
        parent=parent,
    )
    grid.set_cards(
        ProgressionMaterialCard(
            name=material.name,
            amount_text=f"× {material.quantity:,}",
            icon_path=icon_lookup(material.item_id),
            compact=True,
            parent=grid,
        )
        for material in materials
    )
    return grid


__all__ = [
    "CultivationOwnedMaterials",
    "build_material_grid",
    "remaining_materials",
    "visible_owned_inputs",
]
