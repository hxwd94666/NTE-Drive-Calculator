# 展示单个逐击点的结构化重放结果与完整乘区公式。
"""Modeless formula dialog for a selected immutable battle hit."""

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
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuildHitCounterfactual,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
)
from src.services.battle_hit_replay_explanation_service import (
    BattleHitReplayExplanationService,
)
from src.services.skill_name_rendering_service import preferred_battle_damage_name


class BattleHitFormulaDialog(QDialog):
    """Keep one non-modal dialog reusable while the user explores nearby hits."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("逐击伤害公式")
        self.setModal(False)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        self.title_label = QLabel("逐击伤害公式")
        self.title_label.setStyleSheet(
            themed_style("color:#58a6ff;font-size:16px;font-weight:700")
        )
        root.addWidget(self.title_label)

        self.detail = QPlainTextEdit()
        self.detail.setObjectName("battleHitFormulaDetail")
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self.detail, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.hide)
        root.addWidget(buttons)
        fit_dialog_to_available_screen(self, QSize(960, 760))

    def set_hit(
        self,
        hit: BattleAnalysisHit,
        replay: BattleHitReplayResult | None,
        *,
        active_buffs: Sequence[BattleInferredBuffInterval] = (),
        counterfactual: BattleBuildHitCounterfactual | None = None,
    ) -> None:
        damage_name = preferred_battle_damage_name(
            hit.damage_name,
            hit.skill_name,
            hit.ability_id,
        )
        self.title_label.setText(f"{hit.character_name} · {damage_name}")
        self.detail.setPlainText(
            BattleHitReplayExplanationService.build(
                hit,
                replay,
                active_buffs=active_buffs,
                counterfactual=counterfactual,
            )
        )
        self.detail.moveCursor(QTextCursor.MoveOperation.Start)

    def show_for_hit(
        self,
        hit: BattleAnalysisHit,
        replay: BattleHitReplayResult | None,
        *,
        active_buffs: Sequence[BattleInferredBuffInterval] = (),
        counterfactual: BattleBuildHitCounterfactual | None = None,
    ) -> None:
        self.set_hit(
            hit,
            replay,
            active_buffs=active_buffs,
            counterfactual=counterfactual,
        )
        fit_dialog_to_available_screen(self, QSize(960, 760))
        self.show()
        self.raise_()
        self.activateWindow()
