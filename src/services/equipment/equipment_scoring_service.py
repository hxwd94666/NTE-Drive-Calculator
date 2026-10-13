# 提供不依赖页面的驱动、卡带词条评分入口。
"""Public equipment-stat scoring boundary shared by feature presenters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from src.optimizer.scoring import ScoringEngine


@dataclass(frozen=True)
class _DriveScoreInput:
    """适配展示词条到工坊评分器所需的最小驱动对象。"""

    sub_stats: dict[str, float]
    area: int
    quality: str


@dataclass(frozen=True)
class _TapeScoreInput:
    """适配展示词条到工坊评分器所需的最小卡带对象。"""

    main_stats: str
    sub_stats: dict[str, float]
    quality: str
    main_value: float | None


def score_drive_stats(
    engine: ScoringEngine,
    *,
    sub_stat_names: Iterable[str],
    area: int,
    weights: Mapping[str, float],
    quality: str = "Gold",
) -> float:
    """Score one drive from normalized stat names and its occupied area."""

    return engine.calculate_drive_score(
        _DriveScoreInput(
            sub_stats={str(name): 0.0 for name in sub_stat_names},
            area=int(area),
            quality=str(quality),
        ),
        dict(weights),
        engine.max_theoretical_weight(dict(weights)),
    )


def score_tape_stats(
    engine: ScoringEngine,
    *,
    main_stat_name: str,
    sub_stat_names: Iterable[str],
    weights: Mapping[str, float],
    quality: str = "Gold",
    main_weights: Mapping[str, float] | None = None,
    main_value: float | None = None,
) -> float:
    """Score one tape from normalized main/sub-stat names."""

    normalized_weights = dict(weights)
    return engine.calculate_cartridge_score(
        _TapeScoreInput(
            main_stats=str(main_stat_name),
            sub_stats={str(name): 0.0 for name in sub_stat_names},
            quality=str(quality),
            main_value=main_value,
        ),
        normalized_weights,
        engine.max_theoretical_weight(normalized_weights),
        dict(main_weights) if main_weights is not None else None,
    )
