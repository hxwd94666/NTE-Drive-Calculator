# 在一个冻结请求中完成长战报候选、原始副本和目标目录读取。
"""Background-safe orchestration for loading one battle analysis page."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.domain.battle_report import BattleAnalysisSnapshot
from src.services.battle_build_counterfactual_service import (
    BattleBuildCounterfactualService,
)
from src.services.battle_report_history_service import BattleReportHistoryService
from src.services.battle_marginal_candidate_service import BattleMarginalCandidate
from src.services.battle_analysis_progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)


@dataclass(frozen=True, slots=True)
class BattleReportAnalysisLoadRequest:
    battle_record_id: int
    start_us: int | None = None
    end_us: int | None = None
    detail_scope: str | None = None
    detail_level: str = "overview"
    marginal_candidate: BattleMarginalCandidate | None = None
    comparison_baseline: BattleAnalysisSnapshot | None = field(
        default=None,
        compare=False,
        repr=False,
    )


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
        *,
        progress_callback: BattleAnalysisProgressCallback | None = None,
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
        include_buff_counterfactuals = detail_level in {"buff", "marginal"}
        candidate_options = (
            {}
            if candidate is None
            else {"use_build_edit": False, "marginal_candidate": candidate}
        )
        progress_options = (
            {}
            if progress_callback is None
            else {"progress_callback": progress_callback}
        )
        analysis = history.load_analysis(
            request.battle_record_id,
            start_us=request.start_us,
            end_us=request.end_us,
            detail_scope=request.detail_scope,
            include_buff_inference=include_hit_replays,
            include_hit_replays=include_hit_replays,
            include_buff_counterfactuals=include_buff_counterfactuals,
            **progress_options,
            **candidate_options,
        )
        if (
            detail_level == "marginal"
            and analysis is not None
            and candidate is not None
        ):
            baseline = request.comparison_baseline
            if (
                baseline is None
                or baseline.battle_record_id != request.battle_record_id
                or not baseline.hit_replays
                or getattr(baseline, "build_counterfactual", None) is not None
                or baseline.range_start_us != analysis.range_start_us
                or baseline.range_end_us != analysis.range_end_us
                or baseline.target_condition != analysis.target_condition
                or (
                    baseline.target_instance_resolutions
                    != analysis.target_instance_resolutions
                )
                or (
                    baseline.hit_replay_model_version
                    != analysis.hit_replay_model_version
                )
                or (
                    request.start_us is not None
                    and baseline.range_start_us != request.start_us
                )
                or (
                    request.end_us is not None
                    and baseline.range_end_us != request.end_us
                )
            ):
                report_battle_analysis_progress(
                    progress_callback,
                    phase="baseline",
                    message="正在重建原始配置的固定轴基准…",
                )
                baseline = history.load_analysis(
                    request.battle_record_id,
                    start_us=request.start_us,
                    end_us=request.end_us,
                    detail_scope=request.detail_scope,
                    use_build_edit=True,
                    include_buff_inference=True,
                    include_hit_replays=True,
                    include_buff_counterfactuals=False,
                    **progress_options,
                )
            if baseline is not None:
                report_battle_analysis_progress(
                    progress_callback,
                    phase="compare",
                    message="正在汇总修改副本与原始配置差异…",
                )
                analysis = replace(
                    analysis,
                    build_counterfactual=BattleBuildCounterfactualService.compare(
                        original=baseline,
                        candidate=analysis,
                        **progress_options,
                    ),
                )
        if analysis is None or not analysis.timeline_hits:
            return BattleReportAnalysisLoadResult(analysis, None)
        report_battle_analysis_progress(
            progress_callback,
            phase="catalog",
            message="正在读取敌方目标目录…",
        )
        try:
            target_catalog = history.load_target_catalog()
        except Exception as error:
            return BattleReportAnalysisLoadResult(analysis, None, error)
        return BattleReportAnalysisLoadResult(analysis, target_catalog)
