# 汇合战报请求的真实进度事件与整体阶段估计，不拥有页面算法。
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class BattleAnalysisProgress:
    phase: str
    message: str
    completed: int | None = None
    total: int | None = None
    overall_percent: int | None = None

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


# 阶段权重是冻结请求的工作量估计而非计时器；重复阶段按顺序推进，仅成功 complete 到达 100%。
class BattlePageOverallProgress:
    """Stage weights estimate work, not elapsed time; only success reaches 100."""

    def __init__(self, request):
        # Rounded stage proportions calibrated on frozen report page runs.
        # Repeated analyze phases represent initial replay and materialization.
        self._stages = [('load', 1), ('target', 1), ('analyze', 3)]
        if request.detail_level in {'buff', 'marginal'}:
            self._stages.append(('buff_remove', 34))
        if request.detail_level == 'marginal':
            self._stages.append(('analyze', 3))
            if request.selected_character_id is not None:
                if request.marginal_benefit_candidate is not None:
                    self._stages.extend([('core_baseline', 3), ('core_candidates', 44), ('fork', 8)])
                if request.marginal_drive_units is not None:
                    self._stages.append(('panel', 1))
        self._stages.extend([('details', 2), ('serialize', 1), ('decode', 2)])
        self._total = sum(weight for _, weight in self._stages)
        self._index = -1
        self._percent = 1

    def project(self, event: BattleAnalysisProgress) -> BattleAnalysisProgress:
        if event.phase == 'complete':
            self._percent = 100
        else:
            if self._index < 0 or self._stages[self._index][0] != event.phase:
                found = next((index for index in range(self._index + 1, len(self._stages))
                              if self._stages[index][0] == event.phase), None)
                if found is not None:
                    self._index = found
            if self._index >= 0 and self._stages[self._index][0] == event.phase:
                done = sum(weight for _, weight in self._stages[:self._index])
                if event.completed is not None and event.total is not None:
                    fraction = min(1, max(0, event.completed / event.total)) if event.total > 0 else 1
                    done += self._stages[self._index][1] * fraction
                self._percent = max(self._percent, min(99, 1 + int(98 * done / self._total)))
        return replace(event, overall_percent=self._percent)
