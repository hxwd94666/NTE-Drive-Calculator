# 展示逐击日志中单击“推算 Buff”后的结构化加成详情。
"""Reusable modeless dialog for one hit's inferred Buff projection."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report import BattleAnalysisHit, BattleInferredBuffInterval
from src.services.battle_hit_buff_explanation_service import (
    BattleHitBuffExplanationService,
)
from src.services.skill_name_rendering_service import preferred_battle_damage_name


class BattleHitBuffDialog(QDialog):
    """Keep one modeless dialog while the user inspects adjacent hit rows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("逐击推算 Buff")
        self.setModal(False)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        self.title_label = QLabel("逐击推算 Buff")
        self.title_label.setStyleSheet(
            themed_style("color:#58a6ff;font-size:16px;font-weight:700")
        )
        root.addWidget(self.title_label)

        self.detail = QPlainTextEdit()
        self.detail.setObjectName("battleHitBuffDetail")
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self.detail, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.hide)
        root.addWidget(buttons)
        fit_dialog_to_available_screen(self, QSize(980, 760))

    def show_for_hit(
        self,
        hit: BattleAnalysisHit,
        intervals: Sequence[BattleInferredBuffInterval],
    ) -> None:
        damage_name = preferred_battle_damage_name(
            hit.damage_name,
            hit.skill_name,
            hit.ability_id,
        )
        self.title_label.setText(f"{hit.character_name} · {damage_name} · 推算 Buff")
        self.detail.setPlainText(
            BattleHitBuffExplanationService.build(hit, intervals)
        )
        self.detail.moveCursor(QTextCursor.MoveOperation.Start)
        fit_dialog_to_available_screen(self, QSize(980, 760))
        self.show()
        self.raise_()
        self.activateWindow()
