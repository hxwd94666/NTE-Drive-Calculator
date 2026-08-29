# 合并可直接删除的机制逐击与只能保守评估的创生生命周期被动。
"""Compose all creation-passive counterfactual projections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.domain.battle_buff_counterfactual import BattleBuffCounterfactualResult
from src.domain.battle_report import BattleAnalysisSnapshot
from src.services.battle_creation_passive_counterfactual_service import (
    CREATION_PASSIVE_COUNTERFACTUAL_MODEL_VERSION,
    BattleCreationPassiveCounterfactualService,
)
from src.services.battle_creation_passive_evaluation_service import (
    CREATION_PASSIVE_EVALUATION_VERSION,
    BattleCreationPassiveEvaluationService,
)


PASSIVE_COUNTERFACTUAL_MODEL_VERSION = (
    f"{CREATION_PASSIVE_COUNTERFACTUAL_MODEL_VERSION}+"
    f"{CREATION_PASSIVE_EVALUATION_VERSION}"
)


class BattlePassiveCounterfactualService:
    """Return one merge-ready result stream for every enabled creation passive."""

    @staticmethod
    def calculate(
        analysis: BattleAnalysisSnapshot,
        build: Mapping[str, Any] | None,
    ) -> tuple[BattleBuffCounterfactualResult, ...]:
        return (
            *BattleCreationPassiveCounterfactualService.calculate(analysis),
            *BattleCreationPassiveEvaluationService.calculate(analysis, build),
        )


__all__ = [
    "BattlePassiveCounterfactualService",
    "PASSIVE_COUNTERFACTUAL_MODEL_VERSION",
]
