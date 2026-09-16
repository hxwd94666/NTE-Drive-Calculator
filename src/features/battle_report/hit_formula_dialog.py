# 展示单个逐击点的结构化重放结果与完整乘区公式。
"""Modeless formula dialog for a selected immutable battle hit."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QWidget

from src.domain.battle_counterfactual import BattleBuildHitCounterfactual
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
)
from src.services.battle_hit_replay_explanation_service import (
    BattleHitReplayExplanationService,
)
from .hit_inspection_dialog import HitInspectionDialog


class BattleHitFormulaDialog(HitInspectionDialog):
    """Keep one non-modal dialog reusable while the user explores nearby hits."""

    def __init__(self, parent: QWidget | None = None, *, game_ui_asset_root=None) -> None:
        super().__init__(parent, game_ui_asset_root=game_ui_asset_root)
        self.setWindowTitle("逐击伤害公式")
        self.detail.setObjectName("battleHitFormulaDetail")

    def set_hit(
        self,
        hit: BattleAnalysisHit,
        replay: BattleHitReplayResult | None,
        *,
        active_buffs: Sequence[BattleInferredBuffInterval] = (),
        counterfactual: BattleBuildHitCounterfactual | None = None,
        related_counterfactuals: Sequence[BattleBuildHitCounterfactual] = (),
        related_analysis: BattleAnalysisSnapshot | None = None,
        projection=None, related_hit_details=None, participant_names=None, target_resolutions=(),
    ) -> None:
        self.setWindowTitle(f"逐击伤害公式 · Hit #{hit.sequence} · {hit.character_name}")
        self.inspection.set_hit(hit, replay, projection, participant_names=participant_names, target_resolutions=target_resolutions)
        sections = [BattleHitReplayExplanationService.build(
            hit,
            replay,
            active_buffs=active_buffs,
            counterfactual=counterfactual,
            projection=projection, allow_projection_fallback=False,
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
            and row.candidate_damage is not None
        )
        if quantified and related_analysis is not None:
            added = sum(
                row.candidate_damage - row.baseline_damage
                for row in quantified
                if row.candidate_damage is not None
            )
            base = (
                hit.damage
                if counterfactual is None
                else counterfactual.candidate_damage
                if counterfactual.candidate_damage is not None
                else counterfactual.known_projection_damage
                if counterfactual.known_projection_damage is not None
                else counterfactual.baseline_damage
            )
            formula_base = (
                base
                if counterfactual is None or counterfactual.candidate_formula_damage is None
                else counterfactual.candidate_formula_damage
            )
            formula_added = sum(
                row.candidate_damage
                if row.candidate_formula_damage is None
                else row.candidate_formula_damage
                for row in quantified
                if row.candidate_damage is not None
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
                related_projection, related_buffs = ((None, ()) if related_hit_details is None
                    else related_hit_details.for_hit(related_hit, formula=True))
                sections.append(BattleHitReplayExplanationService.build(
                    related_hit,
                    related_replays.get(row.event_id),
                    active_buffs=related_buffs,
                    counterfactual=row,
                    projection=related_projection, allow_projection_fallback=False,
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
        projection=None, related_hit_details=None, participant_names=None, target_resolutions=(),
    ) -> None:
        self.set_hit(
            hit,
            replay,
            active_buffs=active_buffs,
            counterfactual=counterfactual,
            related_counterfactuals=related_counterfactuals,
            related_analysis=related_analysis,
            projection=projection, related_hit_details=related_hit_details, participant_names=participant_names,
            target_resolutions=target_resolutions,
        )
        self.fit_overview()
        self.show()
        self.raise_()
        self.activateWindow()
