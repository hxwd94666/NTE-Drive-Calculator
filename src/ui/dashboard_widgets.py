# 提供 2.0 工作台可复用的小型状态和指标组件。
"""2.0 工作台可复用的小型状态和指标组件。"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from src.app.theme import themed_style


def metric_card(title: str, value: str = "—", subtitle: str = "") -> tuple[QFrame, QLabel, QLabel]:
    card = QFrame()
    card.setObjectName("dashboardMetricCard")
    card.setStyleSheet(
        themed_style(
            "QFrame#dashboardMetricCard{background:#161b22;border:1px solid #21262d;"
            "border-radius:10px;padding:2px}"
        )
    )
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 13, 16, 13)
    layout.setSpacing(4)
    title_label = QLabel(title)
    title_label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    value_label = QLabel(value)
    value_label.setStyleSheet(themed_style("color:#f0f6fc;font-size:25px;font-weight:700"))
    subtitle_label = QLabel(subtitle)
    subtitle_label.setStyleSheet(themed_style("color:#6e7681;font-size:11px"))
    subtitle_label.setWordWrap(True)
    layout.addWidget(title_label)
    layout.addWidget(value_label)
    layout.addWidget(subtitle_label)
    return card, value_label, subtitle_label


def set_status_badge(label: QLabel, text: str, tone: str = "neutral") -> None:
    colors = {
        "success": ("#3fb950", "#23863622"),
        "warning": ("#d2991d", "#d2991d22"),
        "error": ("#f85149", "#f8514922"),
        "active": ("#58a6ff", "#1f6feb33"),
        "neutral": ("#8b949e", "#21262d"),
    }
    foreground, background = colors.get(tone, colors["neutral"])
    label.setText(text)
    label.setStyleSheet(
        themed_style(
            f"color:{foreground};background:{background};border:1px solid {foreground};"
            "border-radius:10px;padding:4px 10px;font-size:11px;font-weight:600"
        )
    )
