# 将属性 Buff 与机制被动边际结果挂载到同一分析。
"""Attach attribute-Buff and mechanism-passive results to one analysis."""

from __future__ import annotations
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo

from collections.abc import Mapping, Sequence
from dataclasses import replace

from src.domain.battle_report.analysis.backend_contracts import DirectFormulaBackend
from src.domain.battle_report.analysis.snapshot import BattleAnalysisSnapshot
from src.domain.battle_report.buffs.models import BattleHitBuffProjection
from src.domain.battle_report.replay.models import BattleSkillDamageEvidence
from src.services.battle_report.buffs.counterfactual import (
    BUFF_COUNTERFACTUAL_MODEL_VERSION,
    BattleBuffCounterfactualService,
)
from src.services.battle_report.damage.passive_counterfactual import (
    PASSIVE_COUNTERFACTUAL_MODEL_VERSION,
    BattlePassiveCounterfactualService,
)
from src.services.battle_report.replay.topple import (
    BattleToppleCharacterConfig,
)
from src.services.battle_report.analysis.progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_report.buffs.interval_index import BattleBuffIntervalIndex


class BattleMarginalCounterfactualProjectionService:
    """Own the immutable final projection for marginal result streams."""

    @staticmethod
    def apply(
        analysis: BattleAnalysisSnapshot,
        build: Mapping[str, object] | None,
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        *,
        topple_character_configs: (
            Mapping[int, BattleToppleCharacterConfig] | None
        ) = None,
        progress_callback: BattleAnalysisProgressCallback | None = None,
        direct_formula_backend: DirectFormulaBackend | None = None,
        interval_index: BattleBuffIntervalIndex | None = None,
        original_projection_by_event: (
            Mapping[str, BattleHitBuffProjection] | None
        ) = None,
        projection_memo: BattleBuffProjectionMemo | None = None,
    ) -> BattleAnalysisSnapshot:
        buff_counterfactuals = BattleBuffCounterfactualService.calculate(
            analysis,
            skill_evidence,
            topple_character_configs=topple_character_configs,
            direct_formula_backend=direct_formula_backend,
            progress_callback=progress_callback,
            interval_index=interval_index,
            original_projection_by_event=original_projection_by_event,
            projection_memo=projection_memo,
        )
        report_battle_analysis_progress(
            progress_callback,
            phase="passive_counterfactual",
            message="正在汇总创生被动与机制收益…",
        )
        passive_counterfactuals = BattlePassiveCounterfactualService.calculate(
            analysis,
            build,
        )
        return replace(
            analysis,
            buff_counterfactuals=buff_counterfactuals,
            buff_counterfactual_model_version=BUFF_COUNTERFACTUAL_MODEL_VERSION,
            passive_counterfactuals=passive_counterfactuals,
            passive_counterfactual_model_version=(
                PASSIVE_COUNTERFACTUAL_MODEL_VERSION
            ),
        )


__all__ = ["BattleMarginalCounterfactualProjectionService"]
