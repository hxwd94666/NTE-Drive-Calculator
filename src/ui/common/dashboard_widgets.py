# 提供 2.0 工作台可复用的小型状态和指标组件。
"""2.0 工作台可复用的小型状态和指标组件。"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

def metric_card(title: str, value: str = "—", subtitle: str = "") -> tuple[QFrame, QLabel, QLabel]:
    card = QFrame()
    card.setObjectName("dashboardMetricCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 13, 16, 13)
    layout.setSpacing(4)
    title_label = QLabel(title)
    title_label.setObjectName("dashboardMetricTitle")
    value_label = QLabel(value)
    value_label.setObjectName("dashboardMetricValue")
    subtitle_label = QLabel(subtitle)
    subtitle_label.setObjectName("dashboardMetricSubtitle")
    subtitle_label.setWordWrap(True)
    layout.addWidget(title_label)
    layout.addWidget(value_label)
    layout.addWidget(subtitle_label)
    return card, value_label, subtitle_label


def set_status_badge(label: QLabel, text: str, tone: str = "neutral") -> None:
    label.setText(text)
    label.setObjectName("statusBadge")
    label.setProperty("tone", tone if tone in {"success", "warning", "error", "active"} else "neutral")
    label.style().unpolish(label)
    label.style().polish(label)
