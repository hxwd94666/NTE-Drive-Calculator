# 提供可复用的空幕与角色属性汇总面板。
"""Reusable two-mode attribute summary panel for allocation result pages."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import current_style_sheet, theme_color, themed_style


@dataclass(frozen=True, slots=True)
class AttributeSummaryRow:
    key: str
    label: str
    value: float
    percent: bool = False
    weight: float = 0.0


@dataclass(frozen=True, slots=True)
class AttributeSummaryLoadout:
    character_id: int
    core: Any | None
    drives: tuple[Any, ...]
    selected_core_type: str | None = None

    @property
    def core_state(self) -> str:
        if self.core is not None:
            return "equipped"
        if self.selected_core_type:
            return "selected_without_item"
        return "empty"


def build_attribute_summary_mode_switch(
    default_mode: str,
    on_change: Callable[[str], None],
    *,
    mode_defs: Sequence[tuple[str, str]] = (
        ("equipment", "空幕属性汇总"),
        ("character", "角色属性汇总"),
    ),
) -> QWidget:
    """Build the shared compact mode switch used by allocation summaries."""

    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    button_group = QButtonGroup(container)
    button_group.setExclusive(True)
    toggle_style = themed_style(
        "QPushButton{background:#161b22;color:#8b949e;border:1px solid #30363d;"
        "border-radius:6px;font-size:12px;font-weight:700;padding:3px 8px;min-height:28px}"
        "QPushButton:checked{background:#1f6feb22;color:#58a6ff;border-color:#58a6ff}"
        "QPushButton:hover{border-color:#58a6ff;color:#c9d1d9}"
    )
    normalized_modes = tuple(mode_defs)
    for index, (mode, label) in enumerate(normalized_modes):
        button = QPushButton(label)
        button.setCheckable(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(toggle_style)
        button_group.addButton(button, index)
        layout.addWidget(button)
        if mode == default_mode:
            button.setChecked(True)

    def mode_clicked(button_id: int) -> None:
        on_change(normalized_modes[button_id][0])

    button_group.idClicked.connect(mode_clicked)
    layout.addStretch()
    return container


class AttributeSummaryPanel(QFrame):
    """Render mode switching, compact rows, and a full-details dialog."""

    @classmethod
    def from_loadout(
        cls,
        role_name: str,
        *,
        core: Any | None,
        drives: Sequence[Any],
        character_id: int,
        selected_core_type: str | None = None,
        rows_provider: Callable[
            [AttributeSummaryLoadout],
            Mapping[str, Sequence[AttributeSummaryRow]],
        ],
        **kwargs: Any,
    ) -> "AttributeSummaryPanel":
        """Build a summary by explicitly passing one core and the drive list."""

        normalized_character_id = int(character_id)
        if normalized_character_id <= 0:
            raise ValueError("character_id 必须大于 0")
        loadout = AttributeSummaryLoadout(
            character_id=normalized_character_id,
            core=core,
            drives=tuple(drive for drive in drives if drive is not None),
            selected_core_type=(
                str(selected_core_type).strip() or None
                if selected_core_type is not None
                else None
            ),
        )
        rows_by_mode = rows_provider(loadout)
        panel = cls(role_name, rows_by_mode, **kwargs)
        panel.loadout = loadout
        panel.setProperty("characterId", loadout.character_id)
        panel.setProperty("coreState", loadout.core_state)
        panel.setProperty("selectedCoreType", loadout.selected_core_type or "")
        return panel

    def __init__(
        self,
        role_name: str,
        rows_by_mode: Mapping[str, Sequence[AttributeSummaryRow]],
        *,
        parent: QWidget | None = None,
        default_mode: str = "equipment",
        color_for_weight: Callable[[float], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._role_name = str(role_name)
        self._rows_by_mode = {
            str(mode): tuple(rows)
            for mode, rows in rows_by_mode.items()
        }
        self._mode = (
            default_mode if default_mode in self._rows_by_mode else "equipment"
        )
        self._color_for_weight = color_for_weight
        self.setMinimumWidth(300)
        self.setFixedHeight(202)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setStyleSheet(themed_style(
            "QFrame{background:#0d1117;border:1px solid #30363d;"
            "border-radius:8px;padding:6px}"
        ))
        root = QVBoxLayout(self)
        root.setContentsMargins(7, 5, 7, 5)
        root.setSpacing(4)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        switch = build_attribute_summary_mode_switch(
            self._mode,
            self.set_mode,
        )
        switch.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        header.addWidget(switch)
        self._more_button = QPushButton("•••")
        self._more_button.setObjectName("btnSm")
        self._more_button.setFixedSize(68, 28)
        self._more_button.setCursor(Qt.PointingHandCursor)
        self._more_button.setStyleSheet(themed_style(
            "QPushButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;"
            "border-radius:8px;font-size:14px;font-weight:800;padding:0}"
            "QPushButton:hover{border-color:#58a6ff;color:#58a6ff}"
        ))
        self._more_button.clicked.connect(self.show_details)
        header.addWidget(self._more_button)
        header.addStretch()
        root.addLayout(header)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("attributeSummaryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setStyleSheet(themed_style(
            "QScrollArea#attributeSummaryScroll{background:transparent;border:none}"
            "QScrollArea#attributeSummaryScroll>QWidget>QWidget{background:transparent}"
            "QScrollBar:vertical{width:8px;background:#0d1117;border:none}"
            "QScrollBar::handle:vertical{background:#484f58;border-radius:4px;min-height:24px}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}"
        ))
        self._content_host = QWidget()
        self._content_host.setStyleSheet("background:transparent")
        self._content = QVBoxLayout(self._content_host)
        self._content.setSizeConstraint(QLayout.SetMinAndMaxSize)
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(4)
        self._scroll.setWidget(self._content_host)
        root.addWidget(self._scroll, 1)
        self._refresh()

    def set_mode(self, mode: str) -> None:
        normalized = str(mode)
        if normalized not in self._rows_by_mode:
            return
        self._mode = normalized
        self._refresh()

    def _clear_content(self) -> None:
        while self._content.count():
            item = self._content.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _refresh(self) -> None:
        self._clear_content()
        rows = self._rows_by_mode.get(self._mode, ())
        self._more_button.setVisible(bool(rows))
        if not rows:
            empty = QLabel("暂无可汇总属性")
            empty.setStyleSheet(themed_style(
                "color:#6e7681;border:none;background:transparent"
            ))
            self._content.addWidget(empty)
            self._content.addStretch()
            self._content_host.adjustSize()
            return
        for row in rows:
            self._content.addWidget(self._row_widget(row))
        self._content.addStretch()
        self._content_host.adjustSize()

    def _row_widget(self, row: AttributeSummaryRow) -> QWidget:
        frame = QFrame()
        frame.setFixedHeight(32)
        frame.setMinimumWidth(130)
        frame.setStyleSheet(themed_style(
            "QFrame{background:#161b22;border:1px solid #21262d;"
            "border-radius:5px;padding:2px 6px}"
        ))
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(6, 1, 6, 1)
        color = (
            self._color_for_weight(row.weight)
            if self._color_for_weight is not None and row.weight > 0
            else theme_color("#8b949e")
        )
        label = QLabel(row.label)
        label.setStyleSheet(
            f"font-size:12px;font-weight:700;color:{color};"
            "border:none;background:transparent"
        )
        value = QLabel(self._value_text(row))
        value.setStyleSheet(
            f"font-size:12px;font-weight:800;color:{theme_color('#f0f6fc')};"
            "border:none;background:transparent"
        )
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(label, 1)
        layout.addWidget(value)
        return frame

    @staticmethod
    def _value_text(row: AttributeSummaryRow) -> str:
        number = f"{float(row.value):.2f}".rstrip("0").rstrip(".")
        return f"+{number}{'%' if row.percent else ''}"

    def show_details(self) -> None:
        rows = self._rows_by_mode.get(self._mode, ())
        if not rows:
            return
        label = (
            "角色属性汇总" if self._mode == "character" else "空幕属性汇总"
        )
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{self._role_name} {label}")
        dialog.setMinimumSize(360, 420)
        dialog.setStyleSheet(current_style_sheet())
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        for row in rows:
            layout.addWidget(self._row_widget(row))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()
