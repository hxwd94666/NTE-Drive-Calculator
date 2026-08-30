# 向角色、弧盘和技能页面提供统一养成体力计算入口。
"""Public Qt-free service for shared progression stamina calculations."""

from __future__ import annotations

from src.domain.progression_stamina import (
    HunterLevelPolicy,
    IdentificationLevelProjection,
    ProgressionStaminaRequest,
    ProgressionStaminaResult,
    calculate_progression_stamina,
    project_identification_level,
)


class ProgressionStaminaService:
    """Own the versioned level policy and delegate immutable pure planning."""

    def __init__(
        self,
        *,
        hunter_level_policy: HunterLevelPolicy = HunterLevelPolicy(),
        maximum_search_states: int = 300_000,
    ) -> None:
        if maximum_search_states <= 0:
            raise ValueError("体力规划搜索上限必须为正整数")
        self._policy = hunter_level_policy
        self._maximum_search_states = int(maximum_search_states)

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
        return calculate_progression_stamina(
            request,
            policy=self._policy,
            maximum_search_states=self._maximum_search_states,
        )
