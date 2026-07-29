# 提供不依赖页面的驱动、卡带词条评分入口。
"""Public equipment-stat scoring boundary shared by feature presenters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from src.optimizer.scoring import ScoringEngine


def score_drive_stats(
    engine: ScoringEngine,
    *,
    sub_stat_names: Iterable[str],
    area: int,
    weights: Mapping[str, float],
    quality: str = "Gold",
) -> float:
    """Score one drive from normalized stat names and its occupied area."""

    maximum_weight = engine.max_theoretical_weight(weights)
    actual_weight = sum(
        engine.flexible_weight(stat_name, weights)
        for stat_name in sub_stat_names
    )
    if actual_weight <= 0 or maximum_weight <= 0 or int(area) <= 0:
        return 0.0
    quality_coefficient = engine.quality_map.get(str(quality), 1.0)
    return round(
        (10.0 / maximum_weight)
        * actual_weight
        * int(area)
        * quality_coefficient,
        2,
    )


def score_tape_stats(
    engine: ScoringEngine,
    *,
    main_stat_name: str,
    sub_stat_names: Iterable[str],
    weights: Mapping[str, float],
    quality: str = "Gold",
    main_weights: Mapping[str, float] | None = None,
) -> float:
    """Score one tape from normalized main/sub-stat names."""

    maximum_weight = engine.max_theoretical_weight(weights)
    quality_coefficient = engine.quality_map.get(str(quality), 1.0)
    main_weight_source = main_weights if main_weights is not None else weights
    main_weight = (
        engine.flexible_weight(main_stat_name, main_weight_source)
        if main_stat_name
        else 0.0
    )
    main_score = main_weight * 50.0 * quality_coefficient
    sub_weight = sum(
        engine.flexible_weight(stat_name, weights)
        for stat_name in sub_stat_names
    )
    sub_score = (
        (10.0 / maximum_weight)
        * sub_weight
        * 10.0
        * quality_coefficient
        if maximum_weight > 0
        else 0.0
    )
    return round(main_score + sub_score, 2)
