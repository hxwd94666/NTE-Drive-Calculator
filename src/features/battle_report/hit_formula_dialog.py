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
    BattleAnalysisSnapshot,
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
        related_counterfactuals: Sequence[BattleBuildHitCounterfactual] = (),
        related_analysis: BattleAnalysisSnapshot | None = None,
    ) -> None:
        damage_name = preferred_battle_damage_name(
            hit.damage_name,
            hit.skill_name,
            hit.ability_id,
        )
        self.title_label.setText(f"{hit.character_name} · {damage_name}")
        sections = [BattleHitReplayExplanationService.build(
            hit,
            replay,
            active_buffs=active_buffs,
            counterfactual=counterfactual,
        )]
        related_hits = {
            row.event_id: row for row in (() if related_analysis is None else related_analysis.hits)
        }
        related_replays = {
            row.event_id: row
            for row in (() if related_analysis is None else related_analysis.hit_replays)
        }
        quantified = tuple(
            row for row in related_counterfactuals
            if row.event_id in related_hits
        )
        if quantified and related_analysis is not None:
            added = sum(row.predicted_damage - row.baseline_damage for row in quantified)
            base = hit.damage if counterfactual is None else counterfactual.predicted_damage
            formula_base = (
                base
                if counterfactual is None or counterfactual.candidate_formula_damage is None
                else counterfactual.candidate_formula_damage
            )
            formula_added = sum(
                row.predicted_damage
                if row.candidate_formula_damage is None
                else row.candidate_formula_damage
                for row in quantified
            )
            sections.append(
                "【关联候选新增结算】\n"
                "上方团队倾陷及所有角色贡献完整保留；下列候选事件只是在同一"
                "触发时点额外追加，不替代原始团队倾陷。\n"
                f"固定轴结算簇 = 上方调整后逐击 {base:,.2f} + "
                f"关联新增 {added:,.2f} = {base + added:,.2f}\n"
                f"候选公式审计合计 = 团队倾陷公式 {formula_base:,.2f} + "
                f"五觉新增公式 {formula_added:,.2f} = "
                f"{formula_base + formula_added:,.2f}"
            )
            for row in quantified:
                related_hit = related_hits[row.event_id]
                related_buffs = tuple(
                    interval
                    for interval in related_analysis.buff_intervals
                    if interval.source_kind != "candidate_derived_awakening_settlement"
                    and interval.start_us <= related_hit.relative_time_us < interval.end_us
                    and (
                        interval.target_scope in {"team", "target", "unknown"}
                        or interval.target_scope == f"character:{related_hit.character_id}"
                        or (
                            interval.target_scope == "self"
                            and interval.source_character_id == related_hit.character_id
                        )
                    )
                )
                sections.append(BattleHitReplayExplanationService.build(
                    related_hit,
                    related_replays.get(row.event_id),
                    active_buffs=related_buffs,
                    counterfactual=row,
                ))
        self.detail.setPlainText(
            "\n\n".join(sections)
        )
        self.detail.moveCursor(QTextCursor.MoveOperation.Start)

    def show_for_hit(
        self,
        hit: BattleAnalysisHit,
        replay: BattleHitReplayResult | None,
        *,
        active_buffs: Sequence[BattleInferredBuffInterval] = (),
        counterfactual: BattleBuildHitCounterfactual | None = None,
        related_counterfactuals: Sequence[BattleBuildHitCounterfactual] = (),
        related_analysis: BattleAnalysisSnapshot | None = None,
    ) -> None:
        self.set_hit(
            hit,
            replay,
            active_buffs=active_buffs,
            counterfactual=counterfactual,
            related_counterfactuals=related_counterfactuals,
            related_analysis=related_analysis,
        )
        fit_dialog_to_available_screen(self, QSize(960, 760))
        self.show()
        self.raise_()
        self.activateWindow()
