# 在战报页面视口底部固定展示按需分析状态。
"""Pinned full-width progress footer for lazy battle analysis."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QWidget,
)

from src.app.theme import themed_style


_DETAIL_COPY = {
    "hit": "正在重建当前范围的 Buff 区间、伤害乘区与逐击公式…",
    "buff": "正在推断 Buff 区间并逐项计算移除反事实…",
    "marginal": "正在重放修改副本与原始配置，并生成固定轴对照…",
    "composition": "正在重放倾陷逐角色公式，并更新伤害构成…",
}


class BattleAnalysisProgressBar(QFrame):
    """Stay below the page stack instead of moving with scroll contents."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("battleAnalysisProgress")
        self.setStyleSheet(themed_style(
            "QFrame#battleAnalysisProgress{"
            "background:#161b22;border-top:1px solid #30363d;}"
            "QLabel{color:#c9d1d9;font-size:12px;font-weight:600;}"
        ))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 8, 18, 8)
        layout.setSpacing(14)
        self.message_label = QLabel()
        self.message_label.setMinimumWidth(360)
        layout.addWidget(self.message_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress, 1)
        self.setFixedHeight(48)
        self.hide()

    def show_for(self, kind: str) -> None:
        self.message_label.setText(
            _DETAIL_COPY.get(kind, "正在重建当前范围的战报分析…")
        )
        self.show()

    def finish(self) -> None:
        self.hide()
