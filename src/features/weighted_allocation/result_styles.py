# 提供加权配装结果页复用的区段标题、权重颜色和布局清理。
"""Small Qt presentation primitives for weighted allocation results."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from src.app.theme import themed_style
from src.ui.attribute_summary_panel import attribute_summary_weight_color


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        if item.layout() is not None:
            clear_layout(item.layout())
            item.layout().deleteLater()


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        themed_style(
            "font-size:14px;font-weight:700;color:#c9d1d9;"
            "border:none;background:transparent;padding:2px 0"
        )
    )
    return label


def weight_color(weight: float) -> str:
    return attribute_summary_weight_color(weight)
