# 在战报页面视口底部固定展示按需分析状态。
"""Pinned full-width progress footer for lazy battle analysis."""

from __future__ import annotations

from src.ui.common.text_catalog import plain_label, resolve_text_catalog
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QProgressBar,
    QWidget,
)

from src.app.theme import themed_style
from src.ui.common.enum_text import ui_enum_text
from src.services.battle_report.analysis.progress import BattleAnalysisProgress


# 分析种类 → 文本目录 key；未知种类使用通用说明。
_DETAIL_COPY = {
    "hit": "ui.battle_report.progress.hit",
    "buff": "ui.battle_report.progress.buff",
    "marginal": "ui.battle_report.progress.marginal",
    "composition": "ui.battle_report.progress.composition",
}


class BattleAnalysisProgressBar(QFrame):
    """Stay below the page stack instead of moving with scroll contents."""

    def __init__(self, parent: QWidget | None = None, *, text_catalog=None) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.setObjectName("battleAnalysisProgress")
        self.setStyleSheet(themed_style(
            "QFrame#battleAnalysisProgress{"
            "background:#161b22;border-top:1px solid #30363d;}"
            "QLabel{color:#c9d1d9;font-size:12px;font-weight:600;}"
        ))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 8, 18, 8)
        layout.setSpacing(14)
        self.message_label = plain_label()
        self.message_label.setMinimumWidth(360)
        layout.addWidget(self.message_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress, 1)
        self.setFixedHeight(48)
        self.hide()

    def show_for(self, kind: str) -> None:
        self.progress.show()
        self.progress.setRange(0, 100)
        self.progress.setValue(1)
        self.progress.setFormat('%p%')
        self.progress.setTextVisible(True)
        self.message_label.setText(
            self.text_catalog.render(_DETAIL_COPY.get(kind, "ui.battle_report.progress.default"))
        )
        self.show()

    def update_progress(self, progress: BattleAnalysisProgress) -> None:
        self.progress.show()
        # 服务进度说明是固定原文表；展示端经冻结目录投影，未登记的诊断原文保持不变。
        message = ui_enum_text(self.text_catalog, progress.message)
        if progress.overall_percent is not None:
            self.progress.setRange(0, 100)
            self.progress.setValue(max(1, min(100, progress.overall_percent)))
            self.progress.setFormat('%p%')
            self.progress.setTextVisible(True)
            if progress.determinate:
                message = f'{message}（{progress.completed}/{progress.total}）'
            self.progress.setToolTip('')
        elif progress.determinate:
            assert progress.completed is not None
            assert progress.total is not None
            completed = max(0, min(progress.completed, progress.total))
            self.progress.setRange(0, progress.total)
            self.progress.setValue(completed)
            self.progress.setFormat(self.text_catalog.render("ui.battle_report.progress.stage_format"))
            self.progress.setTextVisible(True)
            message = f"{message}（{completed}/{progress.total}）"
        else:
            self.progress.setRange(0, 0)
            self.progress.setTextVisible(False)
        self.message_label.setText(message)
        self.show()

    def finish(self) -> None:
        self.hide()

    def show_error(self, message: str) -> None:
        self.progress.hide()
        self.message_label.setWordWrap(True)
        self.message_label.setText(message)
        self.show()
