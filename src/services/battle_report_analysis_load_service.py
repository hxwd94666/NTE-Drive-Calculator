# 在一个冻结请求中完成长战报候选、当前生效基线和目标目录读取。
"""Background-safe orchestration for loading one battle analysis page."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from src.domain.battle_marginal_benefit import BattleMarginalBenefits
from src.domain.battle_report import BattleAnalysisSnapshot
from src.services.battle_build_counterfactual_service import (
    BattleBuildCounterfactualService,
)
from src.services.battle_build_timeline_projection_service import (
    BattleBuildTimelineProjectionService,
)
from src.services.battle_report_history_service import BattleReportHistoryService
from src.services.battle_marginal_candidate_service import BattleMarginalCandidate
from src.services.battle_marginal_benefit_service import (
    BattleMarginalBenefitService,
)
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
    marginal_benefit_candidate: BattleMarginalCandidate | None = None
    selected_character_id: int | None = None
    static_database_path: Path | None = None
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
    marginal_benefits: BattleMarginalBenefits | None = None


class BattleReportAnalysisLoadService:
    """Build the expensive immutable page projection outside the Qt thread."""

    @staticmethod
    def _materialize_marginal_baseline(
        history: BattleReportHistoryService,
        request: BattleReportAnalysisLoadRequest,
        effective: BattleAnalysisSnapshot,
        *,
        progress_callback: BattleAnalysisProgressCallback | None,
        progress_options: dict[str, BattleAnalysisProgressCallback],
    ) -> BattleAnalysisSnapshot:
        """Turn the enabled saved edit into the authoritative current axis."""

        report_battle_analysis_progress(
            progress_callback,
            phase="baseline",
            message="正在读取仅供审计的原始冻结配置…",
        )
        frozen_original = history.load_analysis(
            request.battle_record_id,
            start_us=request.start_us,
            end_us=request.end_us,
            detail_scope=request.detail_scope,
            use_build_edit=False,
            include_buff_inference=True,
            include_hit_replays=True,
            include_buff_counterfactuals=False,
            **progress_options,
        )
        if frozen_original is None:
            return effective
        report_battle_analysis_progress(
            progress_callback,
            phase="compare",
            message="正在物化当前生效配置的固定轴基准…",
        )
        comparison = BattleBuildCounterfactualService.compare(
            original=frozen_original,
            candidate=effective,
            **progress_options,
        )
        projected = BattleBuildTimelineProjectionService.project(
            effective,
            comparison,
        )
        return replace(projected, build_counterfactual=None)

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
        benefit_candidate = request.marginal_benefit_candidate
        if benefit_candidate is not None and detail_level != "marginal":
            raise ValueError("marginal benefits require marginal detail level")
        if (
            benefit_candidate is not None
            and benefit_candidate.battle_record_id != request.battle_record_id
        ):
            raise ValueError(
                "marginal benefit candidate belongs to another battle report"
            )
        include_hit_replays = detail_level != "overview"
        include_buff_counterfactuals = detail_level in {"buff", "marginal"}
        candidate_options = (
            {}
            if candidate is None
            else {"use_build_edit": False, "marginal_candidate": candidate}
        )
        progress_options: dict[str, BattleAnalysisProgressCallback] = (
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
        materialize_baseline = (
            BattleReportAnalysisLoadService._materialize_marginal_baseline
        )
        if (
            detail_level == "marginal"
            and analysis is not None
            and candidate is None
        ):
            analysis = materialize_baseline(
                history,
                request,
                analysis,
                progress_callback=progress_callback,
                progress_options=progress_options,
            )
        elif detail_level == "marginal" and analysis is not None:
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
                    message="正在重建当前生效配置的固定轴基准…",
                )
                effective = history.load_analysis(
                    request.battle_record_id,
                    start_us=request.start_us,
                    end_us=request.end_us,
                    detail_scope=request.detail_scope,
                    include_buff_inference=True,
                    include_hit_replays=True,
                    include_buff_counterfactuals=False,
                    **progress_options,
                )
                baseline = (
                    None
                    if effective is None
                    else materialize_baseline(
                        history,
                        request,
                        effective,
                        progress_callback=progress_callback,
                        progress_options=progress_options,
                    )
                )
            if baseline is not None:
                report_battle_analysis_progress(
                    progress_callback,
                    phase="compare",
                    message="正在汇总草稿与当前生效配置差异…",
                )
                analysis = replace(
                    analysis,
                    build_counterfactual=BattleBuildCounterfactualService.compare(
                        original=baseline,
                        candidate=analysis,
                        **progress_options,
                    ),
                )
        marginal_benefits = None
        if (
            detail_level == "marginal"
            and analysis is not None
            and benefit_candidate is not None
            and request.selected_character_id is not None
        ):
            def load_variant(
                variant: BattleMarginalCandidate,
            ) -> BattleAnalysisSnapshot | None:
                return history.load_analysis(
                    request.battle_record_id,
                    start_us=request.start_us,
                    end_us=request.end_us,
                    detail_scope=request.detail_scope,
                    use_build_edit=False,
                    marginal_candidate=variant,
                    include_buff_inference=True,
                    include_hit_replays=True,
                    include_buff_counterfactuals=False,
                    **progress_options,
                )

            marginal_benefits = BattleMarginalBenefitService.calculate(
                current=analysis,
                candidate=benefit_candidate,
                character_id=request.selected_character_id,
                static_database_path=request.static_database_path,
                load_variant=load_variant,
                progress_callback=progress_callback,
            )
        if analysis is None or not analysis.timeline_hits:
            return BattleReportAnalysisLoadResult(
                analysis,
                None,
                marginal_benefits=marginal_benefits,
            )
        report_battle_analysis_progress(
            progress_callback,
            phase="catalog",
            message="正在读取敌方目标目录…",
        )
        try:
            target_catalog = history.load_target_catalog()
        except Exception as error:
            return BattleReportAnalysisLoadResult(
                analysis=analysis,
                target_catalog=None,
                target_catalog_error=error,
                marginal_benefits=marginal_benefits,
            )
        return BattleReportAnalysisLoadResult(
            analysis,
            target_catalog,
            marginal_benefits=marginal_benefits,
        )
