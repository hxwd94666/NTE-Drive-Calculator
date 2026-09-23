# 提供随可用宽度重排的养成材料卡片网格。
"""Responsive grid for compact progression material cards."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import QGridLayout, QLayout, QSizePolicy, QWidget


class ProgressionMaterialGrid(QWidget):
    """Lay out material cards in stable rows without horizontal scrolling."""

    layout_changed = Signal()

    def __init__(
        self,
        cards: Iterable[QWidget] = (),
        *,
        minimum_card_width: int = 132,
        maximum_columns: int = 5,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("progressionMaterialGrid")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._minimum_card_width = max(96, int(minimum_card_width))
        self._maximum_columns = max(1, int(maximum_columns))
        self._cards: list[QWidget] = []
        self._column_count = 0
        self._grid = QGridLayout(self)
        self._grid.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        self.set_cards(cards)

    @property
    def column_count(self) -> int:
        return self._column_count

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._minimum_card_width, self.height())

    def set_cards(self, cards: Iterable[QWidget]) -> None:
        for card in self._cards:
            self._grid.removeWidget(card)
        self._cards = list(cards)
        self._relayout(force=True)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._relayout()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._relayout(force=True)

    def _relayout(self, *, force: bool = False) -> None:
        spacing = self._grid.horizontalSpacing()
        available = max(self._minimum_card_width, self.width())
        columns = min(
            self._maximum_columns,
            max(1, (available + spacing) // (self._minimum_card_width + spacing)),
        )
        if not force and columns == self._column_count:
            return
        previous_columns = max(self._column_count, self._maximum_columns)
        for column in range(previous_columns):
            self._grid.setColumnStretch(column, 0)
        for card in self._cards:
            self._grid.removeWidget(card)
        self._column_count = columns
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)
        self._grid.invalidate()
        self._grid.activate()
        rows = (len(self._cards) + columns - 1) // columns
        card_height = max(
            (card.sizeHint().height() for card in self._cards),
            default=0,
        )
        height = rows * card_height + max(0, rows - 1) * self._grid.verticalSpacing()
        self.setFixedHeight(height)
        self.updateGeometry()
        self.layout_changed.emit()


__all__ = ["ProgressionMaterialGrid"]
