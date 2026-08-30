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
from src.services.battle_analysis_progress import BattleAnalysisProgress


_DETAIL_COPY = {
    "hit": "正在重建当前范围的 Buff 区间、伤害乘区与逐击公式…",
    "buff": "正在推断 Buff 区间并逐项计算移除反事实…",
    "marginal": "正在物化当前生效基线，并生成固定轴对照…",
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
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.message_label.setText(
            _DETAIL_COPY.get(kind, "正在重建当前范围的战报分析…")
        )
        self.show()

    def update_progress(self, progress: BattleAnalysisProgress) -> None:
        message = progress.message
        if progress.determinate:
            assert progress.completed is not None
            assert progress.total is not None
            completed = max(0, min(progress.completed, progress.total))
            self.progress.setRange(0, progress.total)
            self.progress.setValue(completed)
            self.progress.setFormat("%v / %m")
            self.progress.setTextVisible(True)
            message = f"{message}（{completed}/{progress.total}）"
        else:
            self.progress.setRange(0, 0)
            self.progress.setTextVisible(False)
        self.message_label.setText(message)
        self.show()

    def finish(self) -> None:
        self.hide()
