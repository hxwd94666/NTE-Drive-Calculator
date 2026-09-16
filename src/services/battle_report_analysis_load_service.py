# 在一个冻结请求中完成长战报候选、当前生效基线和目标目录读取。
"""Background-safe orchestration for loading one battle analysis page."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
import warnings

from src.domain.battle_marginal_benefit import BattleMarginalBenefits
from src.domain.battle_report import BattleAnalysisSnapshot
from src.domain.battle_hit_details import BattlePageHitDetails
from src.integrations.nte_analysis_core import NativeAnalysisError
from src.services.battle_buff_projection_memo import BattleBuffProjectionMemo
from src.services.battle_build_counterfactual_service import (
    BattleBuildCounterfactualService,
)
from src.services.battle_build_timeline_projection_service import (
    BattleBuildTimelineProjectionService,
)
from src.services.battle_report_history_service import BattleReportHistoryService
from src.services.battle_report_analysis_inputs import BattleReportAnalysisInputs
from src.services.battle_marginal_candidate_service import BattleMarginalCandidate
from src.services.battle_marginal_benefit_service import (
    BattleMarginalBenefitService,
)
from src.services.battle_marginal_panel_service import BattleMarginalPanelResult, BattleMarginalPanelService
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
    marginal_drive_units: tuple[tuple[str, float], ...] | None = None


@dataclass(frozen=True, slots=True)
class BattleReportAnalysisLoadResult:
    analysis: BattleAnalysisSnapshot | None
    target_catalog: dict[str, object] | None
    target_catalog_error: Exception | None = None
    marginal_benefits: BattleMarginalBenefits | None = None
    marginal_panel: BattleMarginalPanelResult | None = None
    candidate_display_analysis: BattleAnalysisSnapshot | None = None
    hit_details: BattlePageHitDetails | None = None


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
        projection_memo: BattleBuffProjectionMemo,
        frozen_inputs: BattleReportAnalysisInputs,
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
            frozen_inputs=frozen_inputs,
            projection_memo=projection_memo,
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
            projection_memo=projection_memo,
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
        """所有正式战报页统一走原生准备与计算入口，包括边际及反事实。"""
        if history.native_page_loader is None:
            raise NativeAnalysisError('战报分析需要新原生组件，旧计算入口已废弃，不能自动回退')
        return history.native_page_loader.load(request, progress_callback=progress_callback)

    @staticmethod
    def load_legacy_for_differential(
        history: BattleReportHistoryService,
        request: BattleReportAnalysisLoadRequest,
        *,
        progress_callback: BattleAnalysisProgressCallback | None = None,
    ) -> BattleReportAnalysisLoadResult:
        """DEPRECATED：仅供离线新旧差分；正式页面禁止调用。"""
        warnings.warn('旧战报页面计算已废弃，仅供离线差分', DeprecationWarning, stacklevel=2)
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
        projection_memo = history.new_projection_memo(progress_callback=progress_callback) if detail_level == "marginal" else None
        input_options = {}
        if detail_level == "marginal":
            report_battle_analysis_progress(
                progress_callback, phase="load",
                message="正在冻结本次候选共用的战报事实…",
            )
            input_options = {
                "frozen_inputs": history.freeze_analysis_inputs(request.battle_record_id),
                "projection_memo": projection_memo,
            }
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
            **input_options,
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
                projection_memo=projection_memo,
                frozen_inputs=input_options["frozen_inputs"],
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
                    **input_options,
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
                        projection_memo=projection_memo,
                        frozen_inputs=input_options["frozen_inputs"],
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
                        projection_memo=projection_memo,
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
                    **input_options,
                )

            marginal_benefits = BattleMarginalBenefitService.calculate(
                current=analysis,
                candidate=benefit_candidate,
                character_id=request.selected_character_id,
                static_database_path=request.static_database_path,
                load_variant=load_variant,
                progress_callback=progress_callback,
                projection_memo=projection_memo,
            )
        marginal_panel = None
        if (detail_level == "marginal" and analysis is not None
                and request.selected_character_id is not None and request.marginal_drive_units is not None):
            report_battle_analysis_progress(progress_callback, phase="marginal_panel", message="正在计算人物动态面板与属性边际…")
            marginal_panel = BattleMarginalPanelService.calculate(
                analysis=analysis, character_id=request.selected_character_id,
                drive_units=dict(request.marginal_drive_units), projection_memo=projection_memo,
                progress_callback=progress_callback,
            )
            report_battle_analysis_progress(progress_callback, phase="marginal_panel", message="人物动态面板与属性边际已完成")
        display_comparison = getattr(analysis, "build_counterfactual", None)
        candidate_display_analysis = (
            BattleBuildTimelineProjectionService.project(analysis, display_comparison)
            if analysis is not None and display_comparison is not None
            else None
        )
        if analysis is None or not analysis.timeline_hits:
            return BattleReportAnalysisLoadResult(
                analysis,
                None,
                marginal_benefits=marginal_benefits,
                marginal_panel=marginal_panel,
                candidate_display_analysis=candidate_display_analysis,
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
                marginal_panel=marginal_panel,
                candidate_display_analysis=candidate_display_analysis,
            )
        return BattleReportAnalysisLoadResult(
            analysis,
            target_catalog,
            marginal_benefits=marginal_benefits,
            marginal_panel=marginal_panel,
            candidate_display_analysis=candidate_display_analysis,
        )
