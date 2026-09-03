# 提供养成计算器的角色与弧盘选择控件。
"""Single-choice image-card selectors for the toolbox cultivation calculator."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.ui.widgets import match_pinyin


class CultivationImageSelector(QDialog):
    """A searchable, one-of-many image card dialog matching role-scope selection."""

    def __init__(
        self,
        parent: QWidget,
        *,
        title: str,
        description: str,
        options: tuple[tuple[str, str, str | None], ...],
        selected_id: str | None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("cultivationImageSelector")
        self._options = options
        self._cards: list[tuple[QToolButton, str, str]] = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._build(description, selected_id)
        fit_dialog_to_available_screen(self, QSize(760, 620))

    def _build(self, description: str, selected_id: str | None) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)
        note = QLabel(description, self)
        note.setWordWrap(True)
        note.setStyleSheet(themed_style("color:#8b949e"))
        root.addWidget(note)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("搜索（支持拼音）")
        self._search.textChanged.connect(self._apply_filter)
        root.addWidget(self._search)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._grid_widget = QWidget(self._scroll)
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        self._grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        for option_id, name, icon_path in self._options:
            card = QToolButton(self._grid_widget)
            card.setCheckable(True)
            card.setChecked(option_id == selected_id)
            card.setText(name)
            card.setToolTip(name)
            card.setFixedSize(116, 132)
            card.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            card.setIconSize(QSize(76, 76))
            if icon_path:
                card.setIcon(QIcon(icon_path))
            card.setStyleSheet(themed_style(
                "QToolButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;"
                "border-radius:8px;padding:6px;font-size:12px;font-weight:700;}"
                "QToolButton:hover{border-color:#58a6ff;background:#1f6feb22;}"
                "QToolButton:checked{border:2px solid #58a6ff;background:#1f6feb;color:#fff;}"
            ))
            self._group.addButton(card)
            self._cards.append((card, option_id, name))
        self._reflow()
        self._scroll.setWidget(self._grid_widget)
        root.addWidget(self._scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _apply_filter(self, text: str) -> None:
        self._reflow(str(text or "").strip())

    def _reflow(self, keyword: str = "") -> None:
        while self._grid.count():
            self._grid.takeAt(0)
        visible = [
            item for item in self._cards
            if not keyword or match_pinyin(item[2], keyword)
        ]
        for card, _option_id, _name in self._cards:
            card.setVisible(False)
        for index, (card, _option_id, _name) in enumerate(visible):
            self._grid.addWidget(card, index // 5, index % 5)
            card.setVisible(True)

    def selected_id(self) -> str | None:
        return next((
            option_id for card, option_id, _name in self._cards if card.isChecked()
        ), None)


def select_cultivation_item(
    parent: QWidget,
    *,
    title: str,
    description: str,
    options: tuple[tuple[str, str, str | None], ...],
    selected_id: str | None,
) -> str | None:
    """Run a selector and return the newly confirmed identity, if any."""

    dialog = CultivationImageSelector(
        parent,
        title=title,
        description=description,
        options=options,
        selected_id=selected_id,
    )
    return dialog.selected_id() if dialog.exec() == QDialog.DialogCode.Accepted else None


__all__ = ["CultivationImageSelector", "select_cultivation_item"]
