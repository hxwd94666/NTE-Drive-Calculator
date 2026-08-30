# 描述后台战报分析的真实阶段与可计数工作量。
"""Qt-free progress events for long battle-analysis work."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BattleAnalysisProgress:
    phase: str
    message: str
    completed: int | None = None
    total: int | None = None

    @property
    def determinate(self) -> bool:
        return (
            self.completed is not None
            and self.total is not None
            and self.total > 0
        )


BattleAnalysisProgressCallback = Callable[[BattleAnalysisProgress], None]


def report_battle_analysis_progress(
    callback: BattleAnalysisProgressCallback | None,
    *,
    phase: str,
    message: str,
    completed: int | None = None,
    total: int | None = None,
) -> None:
    if callback is None:
        return
    callback(BattleAnalysisProgress(
        phase=phase,
        message=message,
        completed=completed,
        total=total,
    ))


__all__ = [
    "BattleAnalysisProgress",
    "BattleAnalysisProgressCallback",
    "report_battle_analysis_progress",
]
