# 构建工具页的游戏资料库入口卡片。
"""Small themed entry widget extracted to keep the toolbox page bounded."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from src.app.theme import themed_style


def build_static_catalog_entry(
    parent: QWidget, *, navigate: Callable[[], None]
) -> QFrame:
    row = QFrame(parent)
    row.setObjectName("toolboxStaticCatalogRow")
    row.setMinimumHeight(94)
    row.setStyleSheet(themed_style(
        "QFrame#toolboxStaticCatalogRow{background:#161b22;border:1px solid #30363d;"
        "border-radius:10px;}QFrame#toolboxStaticCatalogRow:hover{background:#1c2128;"
        "border-color:#58a6ff;}"
    ))
    layout = QHBoxLayout(row)
    layout.setContentsMargins(18, 12, 16, 12)
    layout.setSpacing(15)
    copy = QVBoxLayout()
    copy.setSpacing(4)
    title = QLabel("游戏资料库", row)
    title.setStyleSheet(themed_style("font-size:16px;font-weight:800;color:#58a6ff"))
    copy.addWidget(title)
    description = QLabel(
        "只读浏览角色、弧盘、怪物、装备、正式技能/效果、公式证据与 110 表覆盖总览。",
        row,
    )
    description.setWordWrap(True)
    description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    copy.addWidget(description)
    layout.addLayout(copy, 1)
    button = QPushButton("打开", row)
    button.setObjectName("toolboxStaticCatalog")
    button.setCursor(Qt.PointingHandCursor)
    button.setMinimumSize(76, 38)
    button.setStyleSheet(themed_style(
        "QPushButton{background:#d6f0ff;color:#0b3150;border:1px solid #79c0ff;"
        "border-radius:7px;font-size:13px;font-weight:800;padding:6px 16px;}"
        "QPushButton:hover{background:#b6e3ff;border-color:#a5d6ff;}"
        "QPushButton:pressed{background:#9ed5f5;}"
    ))
    button.clicked.connect(navigate)
    layout.addWidget(button, 0, Qt.AlignVCenter)
    return row
