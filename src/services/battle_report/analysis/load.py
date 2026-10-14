# 冻结页面请求并只派发原生分析，缺失组件或失败均不回退旧计算。
"""正式页面只派发原生加载；旧Python差分保留在历史服务供离线工具使用。"""

from __future__ import annotations

import src.domain.battle_report.analysis.page_results as battle_page_models

from dataclasses import dataclass, field
from pathlib import Path

from src.domain.battle_report.analysis.snapshot import BattleAnalysisSnapshot
from src.integrations.nte_analysis_core import NativeAnalysisError
from src.services.battle_report.history.service import BattleReportHistoryService
from src.domain.battle_report.counterfactual.results import BattleMarginalCandidate
from src.services.battle_report.analysis.progress import (
    BattleAnalysisProgressCallback,
)


@dataclass(frozen=True, slots=True)
class BattleReportAnalysisLoadRequest:
    battle_record_id: int
    start_us: int | None = None
    end_us: int | None = None
    detail_scope: str | None = None
    detail_level: str = "overview"
    marginal_candidate: BattleMarginalCandidate | None = None
    marginal_benefit_candidate: BattleMarginalCandidate | None = None
    selected_character_id: int | None = None
    static_database_path: Path | None = None
    comparison_baseline: BattleAnalysisSnapshot | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    marginal_drive_units: tuple[tuple[str, float], ...] | None = None


class BattleReportAnalysisLoadService:
    """后台统一入口只使用原生页面owner，原始事实与派生展示保持分离。"""


    @staticmethod
    def load(
        history: BattleReportHistoryService,
        request: BattleReportAnalysisLoadRequest,
        *,
        progress_callback: BattleAnalysisProgressCallback | None = None,
    ) -> battle_page_models.BattleReportAnalysisLoadResult:
        """所有正式战报页统一走原生准备与计算入口，包括边际及反事实。"""
        if history.native_page_loader is None:
            raise NativeAnalysisError('战报分析需要新原生组件，旧计算入口已废弃，不能自动回退')
        return history.native_page_loader.load(request, progress_callback=progress_callback)

