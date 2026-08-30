# 向角色、弧盘和技能页面提供统一养成体力计算入口。
"""Public Qt-free service for shared progression stamina calculations."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from src.domain.progression_stamina import (
    FarmingStage,
    HunterLevelPolicy,
    IdentificationLevelProjection,
    ProgressionStaminaRequest,
    ProgressionStaminaResult,
    calculate_progression_stamina,
    project_identification_level,
)


class OfficialFarmingStageSource(Protocol):
    """Narrow read-only source for formal deterministic farming stages."""

    def list_progression_farming_stages(self) -> tuple[FarmingStage, ...]: ...


class ProgressionStaminaService:
    """Own the versioned level policy and delegate immutable pure planning."""

    def __init__(
        self,
        *,
        hunter_level_policy: HunterLevelPolicy = HunterLevelPolicy(),
        maximum_search_states: int = 300_000,
        official_stage_source: OfficialFarmingStageSource | None = None,
    ) -> None:
        if maximum_search_states <= 0:
            raise ValueError("体力规划搜索上限必须为正整数")
        self._policy = hunter_level_policy
        self._maximum_search_states = int(maximum_search_states)
        self._official_stage_source = official_stage_source

    def identification_level(
        self,
        hunter_level: int,
        *,
        effective_level: int | None = None,
    ) -> IdentificationLevelProjection:
        return project_identification_level(
            hunter_level,
            effective_level=effective_level,
            policy=self._policy,
        )

    def calculate(
        self,
        request: ProgressionStaminaRequest,
    ) -> ProgressionStaminaResult:
        effective_request = request
        if not request.stages and self._official_stage_source is not None:
            official_stages = (
                self._official_stage_source.list_progression_farming_stages()
            )
            effective_request = replace(request, stages=official_stages)
        return calculate_progression_stamina(
            effective_request,
            policy=self._policy,
            maximum_search_states=self._maximum_search_states,
        )
