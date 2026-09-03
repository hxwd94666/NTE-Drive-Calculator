# 提供工具页养成计算器的入口卡片。
"""Entry card and dialog launcher for the toolbox cultivation calculator."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.toolbox.cultivation_calculator import CultivationCalculatorDialog
from src.services.cultivation_planner_service import CultivationPlannerService


def build_cultivation_calculator_entry(
    parent: QWidget,
    *,
    open_calculator: Callable[[], None],
) -> QWidget:
    """Build the small toolbox card without coupling the page to dialog details."""

    row = QFrame(parent)
    row.setObjectName("toolboxCultivationCalculatorRow")
    row.setMinimumHeight(94)
    row.setStyleSheet(themed_style(
        "QFrame#toolboxCultivationCalculatorRow{background:#161b22;border:1px solid #30363d;"
        "border-radius:10px;}QFrame#toolboxCultivationCalculatorRow:hover{background:#1c2128;border-color:#58a6ff;}"
    ))
    layout = QHBoxLayout(row)
    layout.setContentsMargins(18, 12, 16, 12)
    layout.setSpacing(15)
    copy = QVBoxLayout()
    copy.setSpacing(4)
    title = QLabel("养成计算器", row)
    title.setStyleSheet(themed_style("font-size:16px;font-weight:800;color:#58a6ff"))
    copy.addWidget(title)
    description = QLabel(
        "按角色等级、突破和技能目标汇总官方养成材料；当前不扣除背包，也不估算体力。",
        row,
    )
    description.setWordWrap(True)
    description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    copy.addWidget(description)
    layout.addLayout(copy, 1)
    button = QPushButton("使用", row)
    button.setObjectName("toolboxCultivationCalculator")
    button.setCursor(Qt.PointingHandCursor)
    button.setMinimumSize(76, 38)
    button.setStyleSheet(themed_style(
        "QPushButton{background:#d6f0ff;color:#0b3150;border:1px solid #79c0ff;border-radius:7px;"
        "font-size:13px;font-weight:800;padding:6px 16px;}"
        "QPushButton:hover{background:#b6e3ff;border-color:#a5d6ff;}"
        "QPushButton:pressed{background:#9ed5f5;}"
    ))
    button.clicked.connect(open_calculator)
    layout.addWidget(button, 0, Qt.AlignVCenter)
    return row


def show_cultivation_calculator(
    parent: QWidget,
    *,
    service_factory: Callable[[], CultivationPlannerService],
) -> None:
    """Construct the account-bound service only when the user opens the tool."""

    try:
        service = service_factory()
    except Exception as exc:
        QMessageBox.warning(parent, "养成计算器", f"读取养成数据失败：{exc}")
        return
    CultivationCalculatorDialog(service, parent).exec()


__all__ = ["build_cultivation_calculator_entry", "show_cultivation_calculator"]
