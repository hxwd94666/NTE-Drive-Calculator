# 在一个冻结请求中完成长战报候选、原始副本和目标目录读取。
"""Background-safe orchestration for loading one battle analysis page."""

from __future__ import annotations

from dataclasses import dataclass, replace

from src.domain.battle_report import BattleAnalysisSnapshot
from src.services.battle_build_counterfactual_service import (
    BattleBuildCounterfactualService,
)
from src.services.battle_report_history_service import BattleReportHistoryService
from src.services.battle_marginal_candidate_service import BattleMarginalCandidate


@dataclass(frozen=True, slots=True)
class BattleReportAnalysisLoadRequest:
    battle_record_id: int
    start_us: int | None = None
    end_us: int | None = None
    detail_scope: str | None = None
    detail_level: str = "overview"
    marginal_candidate: BattleMarginalCandidate | None = None


@dataclass(frozen=True, slots=True)
class BattleReportAnalysisLoadResult:
    analysis: BattleAnalysisSnapshot | None
    target_catalog: dict[str, object] | None
    target_catalog_error: Exception | None = None


class BattleReportAnalysisLoadService:
    """Build the expensive immutable page projection outside the Qt thread."""

    @staticmethod
    def load(
        history: BattleReportHistoryService,
        request: BattleReportAnalysisLoadRequest,
    ) -> BattleReportAnalysisLoadResult:
        detail_level = request.detail_level
        if detail_level not in {"overview", "hit", "buff", "marginal"}:
            raise ValueError(f"unsupported battle analysis detail: {detail_level}")
        candidate = request.marginal_candidate
        if candidate is not None and detail_level != "marginal":
            raise ValueError("marginal candidate requires marginal detail level")
        if (
            candidate is not None
            and candidate.battle_record_id != request.battle_record_id
        ):
            raise ValueError("marginal candidate belongs to another battle report")
        include_hit_replays = detail_level != "overview"
        include_buff_counterfactuals = detail_level == "buff"
        candidate_options = (
            {}
            if candidate is None
            else {"use_build_edit": False, "marginal_candidate": candidate}
        )
        analysis = history.load_analysis(
            request.battle_record_id,
            start_us=request.start_us,
            end_us=request.end_us,
            detail_scope=request.detail_scope,
            include_buff_inference=include_hit_replays,
            include_hit_replays=include_hit_replays,
            include_buff_counterfactuals=include_buff_counterfactuals,
            **candidate_options,
        )
        if (
            detail_level == "marginal"
            and analysis is not None
            and candidate is not None
        ):
            original = history.load_analysis(
                request.battle_record_id,
                start_us=request.start_us,
                end_us=request.end_us,
                detail_scope=request.detail_scope,
                use_build_edit=False,
                include_buff_inference=True,
                include_hit_replays=True,
                include_buff_counterfactuals=False,
            )
            if original is not None:
                analysis = replace(
                    analysis,
                    build_counterfactual=BattleBuildCounterfactualService.compare(
                        original=original,
                        candidate=analysis,
                    ),
                )
        if analysis is None or not analysis.timeline_hits:
            return BattleReportAnalysisLoadResult(analysis, None)
        try:
            target_catalog = history.load_target_catalog()
        except Exception as error:
            return BattleReportAnalysisLoadResult(analysis, None, error)
        return BattleReportAnalysisLoadResult(analysis, target_catalog)
