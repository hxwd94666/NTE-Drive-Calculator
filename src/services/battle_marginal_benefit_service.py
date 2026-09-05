# 在同一战报固定轴上计算空幕金色主属性候选与弧盘综合收益。
"""Selected-role equipment and fork counterfactual orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import replace
from math import isclose
from pathlib import Path
from typing import Any

from src.domain.battle_counterfactual import BattleBuildCounterfactual
from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
)
from src.domain.battle_marginal_benefit import (
    BattleCoreMainStatMarginal,
    BattleForkMarginal,
    BattleMarginalBenefits,
    BattleMarginalDelta,
)
from src.domain.battle_report import BattleAnalysisSnapshot
from src.services.battle_analysis_progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_build_counterfactual_service import (
    BattleBuildCounterfactualService,
)
from src.services.battle_build_awakening_gap_service import (
    awakening_gaps_for_character,
    with_awakening_gaps,
)
from src.services.battle_build_quantification_service import (
    BattleBuildQuantificationService,
)
from src.services.battle_build_timeline_projection_service import (
    BattleBuildTimelineProjectionService,
)
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidate,
    BattleMarginalCandidateService,
)
from src.services.battle_marginal_benefit_scope import (
    BattleMarginalBenefitRoleScope,
    marginal_benefit_role_rows,
    observed_marginal_benefit_role_damage,
    prepare_marginal_benefit_role_scope,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


LoadVariant = Callable[[BattleMarginalCandidate], BattleAnalysisSnapshot | None]
_CORE_MAIN_PROPERTIES = (
    "HPMaxUp",
    "AtkUp",
    "DefUp",
    "CritBase",
    "CritDamageBase",
    "MagBase",
    "UnbalIntensityBase",
    "HealUp",
    "DamageUpCosmosBase",
    "DamageUpNatureBase",
    "DamageUpIncantationBase",
    "DamageUpChaosBase",
    "DamageUpPsycheBase",
    "DamageUpLakshanaBase",
    "DamageUpPsychicallyBase",
)


class BattleMarginalBenefitService:
    """Calculate decision-oriented equipment benefits outside the Qt thread."""

    @classmethod
    def calculate(
        cls,
        *,
        current: BattleAnalysisSnapshot,
        candidate: BattleMarginalCandidate,
        character_id: int,
        static_database_path: str | Path | None,
        load_variant: LoadVariant,
        progress_callback: BattleAnalysisProgressCallback | None = None,
    ) -> BattleMarginalBenefits:
        current = cls._materialize_current(current)
        profile = cls._profile(candidate, character_id)
        if profile is None:
            return BattleMarginalBenefits(
                character_id=character_id,
                core_notice="当前边际候选中没有该角色配置。",
            )
        baseline = next(
            (row for row in current.baselines if row.character_id == character_id),
            None,
        )
        if baseline is None:
            return BattleMarginalBenefits(
                character_id=character_id,
                core_notice="当前固定轴缺少该角色面板基线。",
            )
        role_scope = prepare_marginal_benefit_role_scope(current, character_id)

        core_catalog, fork_names = cls._static_catalog(
            static_database_path,
            level=20,
        )
        core_rows, core_notice = cls._core_main_stats(
            current=current,
            candidate=candidate,
            profile=profile,
            character_id=character_id,
            role_scope=role_scope,
            core_catalog=core_catalog,
            load_variant=load_variant,
            progress_callback=progress_callback,
        )
        fork = cls._fork_benefit(
            current=current,
            candidate=candidate,
            profile=profile,
            character_id=character_id,
            role_scope=role_scope,
            fork_names=fork_names,
            load_variant=load_variant,
            progress_callback=progress_callback,
        )
        return BattleMarginalBenefits(
            character_id=character_id,
            core_main_stats=core_rows,
            core_notice=core_notice,
            fork=fork,
        )

    @classmethod
    def _core_main_stats(
        cls,
        *,
        current: BattleAnalysisSnapshot,
        candidate: BattleMarginalCandidate,
        profile: Mapping[str, Any],
        character_id: int,
        role_scope: BattleMarginalBenefitRoleScope,
        core_catalog: Mapping[str, tuple[str, bool, float]],
        load_variant: LoadVariant,
        progress_callback: BattleAnalysisProgressCallback | None,
    ) -> tuple[tuple[BattleCoreMainStatMarginal, ...], str]:
        core = cls._core(profile)
        if core is None:
            return (), "当前角色没有可用于反事实的空幕副本。"
        current_main = next(
            (
                row for row in core.get("stats") or ()
                if isinstance(row, Mapping)
                and str(row.get("stat_group") or "") == "main"
            ),
            None,
        )
        if current_main is None:
            return (), "当前空幕缺少正式主属性记录。"
        current_property = str(current_main.get("property_id") or "").strip()
        if not current_property:
            return (), "当前空幕主属性标识无效。"
        if not core_catalog:
            return (), "官方金色空幕主属性曲线不可用。"

        no_main_candidate = BattleMarginalCandidateService.with_core_main_stat(
            candidate, character_id, None,
        )
        report_battle_analysis_progress(
            progress_callback,
            phase="core_main",
            message="正在计算金色空幕无主属性基线…",
            completed=0,
            total=len(core_catalog),
        )
        loaded_no_main = load_variant(no_main_candidate)
        if loaded_no_main is None:
            return (), "无法建立金色空幕无主属性固定轴基线。"
        no_main = cls._materialize_variant(
            current,
            replace(loaded_no_main, build_counterfactual=None),
            progress_callback=progress_callback,
        )
        current_from_no_main = BattleBuildCounterfactualService.compare(
            original=no_main,
            candidate=current,
            progress_callback=progress_callback,
        )

        rows: list[BattleCoreMainStatMarginal] = []
        for ordinal, property_id in enumerate(_CORE_MAIN_PROPERTIES, start=1):
            definition = core_catalog.get(property_id)
            if definition is None:
                continue
            label, is_percent, official_value = definition
            is_current = property_id == current_property
            value = official_value
            matches_current = is_current and cls._same_stat_value(
                value,
                float(current_main.get("value") or 0.0),
            )
            if matches_current:
                variant = current
                replacement = cls._unchanged_delta(
                    current,
                    role_scope,
                )
                contribution_comparison = current_from_no_main
            else:
                variant_candidate = BattleMarginalCandidateService.with_core_main_stat(
                    candidate,
                    character_id,
                    {
                        "stat_group": "main",
                        "ordinal": 0,
                        "property_id": property_id,
                        "value": value,
                        "is_percent": is_percent,
                        "names": {"zh": label},
                    },
                )
                report_battle_analysis_progress(
                    progress_callback,
                    phase="core_main",
                    message=f"正在计算金色空幕主属性：{label}…",
                    completed=ordinal - 1,
                    total=len(core_catalog),
                )
                loaded_variant = load_variant(variant_candidate)
                if loaded_variant is None:
                    continue
                loaded_variant = replace(
                    loaded_variant,
                    build_counterfactual=None,
                )
                replacement_comparison = BattleBuildCounterfactualService.compare(
                    original=current,
                    candidate=loaded_variant,
                    progress_callback=progress_callback,
                )
                variant = BattleBuildTimelineProjectionService.project(
                    loaded_variant,
                    replacement_comparison,
                )
                contribution_comparison = BattleBuildCounterfactualService.compare(
                    original=no_main,
                    candidate=variant,
                    progress_callback=progress_callback,
                )
                replacement = cls._delta(
                    replacement_comparison,
                    role_scope,
                )
            rows.append(BattleCoreMainStatMarginal(
                property_id=property_id,
                label=label,
                value=value,
                is_percent=is_percent,
                is_current=is_current,
                contribution=cls._delta(
                    contribution_comparison,
                    role_scope,
                ),
                replacement=replacement,
            ))
        report_battle_analysis_progress(
            progress_callback,
            phase="core_main",
            message="金色空幕主属性边际已汇总。",
            completed=len(core_catalog),
            total=len(core_catalog),
        )
        return tuple(rows), ""

    @classmethod
    def _fork_benefit(
        cls,
        *,
        current: BattleAnalysisSnapshot,
        candidate: BattleMarginalCandidate,
        profile: Mapping[str, Any],
        character_id: int,
        role_scope: BattleMarginalBenefitRoleScope,
        fork_names: Mapping[str, str],
        load_variant: LoadVariant,
        progress_callback: BattleAnalysisProgressCallback | None,
    ) -> BattleForkMarginal | None:
        fork_id = str(profile.get("fork_id") or "").strip()
        if not fork_id:
            return BattleForkMarginal(
                fork_id="",
                fork_name="未装备弧盘",
                no_fork_team_damage=None,
                no_fork_role_damage=None,
                permanent=None,
                skill=None,
                comprehensive=None,
                unavailable_reason="当前角色未装备弧盘。",
            )
        fork_name = fork_names.get(fork_id, fork_id)
        no_fork_profile = deepcopy(dict(profile))
        for key in (
            "fork_id",
            "fork_level",
            "fork_breakthrough_stage",
            "fork_refinement_level",
        ):
            no_fork_profile[key] = None
        report_battle_analysis_progress(
            progress_callback,
            phase="fork_benefit",
            message=f"正在建立 {fork_name} 的无弧盘基线…",
        )
        no_fork_candidate = cls._replace_profile(
            candidate, character_id, no_fork_profile,
        )
        loaded_no_fork = load_variant(no_fork_candidate)
        if loaded_no_fork is None:
            return BattleForkMarginal(
                fork_id=fork_id,
                fork_name=fork_name,
                no_fork_team_damage=None,
                no_fork_role_damage=None,
                permanent=None,
                skill=None,
                comprehensive=None,
                unavailable_reason="无法建立无弧盘固定轴基线。",
            )
        no_fork = cls._materialize_variant(
            current,
            replace(loaded_no_fork, build_counterfactual=None),
            progress_callback=progress_callback,
        )

        current_baseline = next(
            row for row in current.baselines if row.character_id == character_id
        )
        stats_only_profile = deepcopy(no_fork_profile)
        stats_only_profile["battle_stat_overrides"] = {
            row.property_id: float(row.value) for row in current_baseline.stats
        }
        stats_only_candidate = cls._replace_profile(
            candidate, character_id, stats_only_profile,
        )
        report_battle_analysis_progress(
            progress_callback,
            phase="fork_benefit",
            message=f"正在拆分 {fork_name} 的常驻属性与技能机制…",
        )
        loaded_stats_only = load_variant(stats_only_candidate)
        if loaded_stats_only is None:
            return BattleForkMarginal(
                fork_id=fork_id,
                fork_name=fork_name,
                no_fork_team_damage=float(no_fork.effective_damage),
                no_fork_role_damage=cls._observed_panel_damage(
                    no_fork,
                    role_scope,
                ),
                permanent=None,
                skill=None,
                comprehensive=None,
                unavailable_reason="无法建立仅保留弧盘常驻面板的分析状态。",
            )
        stats_only = cls._materialize_variant(
            current,
            replace(loaded_stats_only, build_counterfactual=None),
            progress_callback=progress_callback,
        )
        comprehensive_comparison = BattleBuildCounterfactualService.compare(
            original=no_fork,
            candidate=current,
            progress_callback=progress_callback,
        )
        permanent_comparison = BattleBuildCounterfactualService.compare(
            original=no_fork,
            candidate=stats_only,
            progress_callback=progress_callback,
        )
        skill_comparison = BattleBuildCounterfactualService.compare(
            original=stats_only,
            candidate=current,
            progress_callback=progress_callback,
        )
        current_role_damage = cls._observed_panel_damage(
            current,
            role_scope,
        )
        stats_only_role_damage = cls._observed_panel_damage(
            stats_only,
            role_scope,
        )
        comprehensive = cls._delta(
            comprehensive_comparison,
            role_scope,
            team_endpoint_damage=float(current.effective_damage),
            role_endpoint_damage=current_role_damage,
        )
        permanent = cls._delta(
            permanent_comparison,
            role_scope,
            team_endpoint_damage=float(stats_only.effective_damage),
            role_endpoint_damage=stats_only_role_damage,
        )
        skill = cls._delta(
            skill_comparison,
            role_scope,
            team_endpoint_damage=float(current.effective_damage),
            role_endpoint_damage=current_role_damage,
            team_percent_denominator=comprehensive.baseline_team_damage,
            role_percent_denominator=comprehensive.baseline_role_damage,
        )
        closure_team = cls._closure(
            permanent.team_gain_damage,
            skill.team_gain_damage,
            comprehensive.team_gain_damage,
        )
        closure_role = cls._closure(
            permanent.role_gain_damage,
            skill.role_gain_damage,
            comprehensive.role_gain_damage,
        )
        return BattleForkMarginal(
            fork_id=fork_id,
            fork_name=fork_name,
            no_fork_team_damage=comprehensive.baseline_team_damage,
            no_fork_role_damage=comprehensive.baseline_role_damage,
            permanent=permanent,
            skill=skill,
            comprehensive=comprehensive,
            closure_team_damage=closure_team,
            closure_role_damage=closure_role,
        )

    @staticmethod
    def _profile(
        candidate: BattleMarginalCandidate,
        character_id: int,
    ) -> dict[str, Any] | None:
        return next(
            (
                deepcopy(profile)
                for profile in candidate.profiles
                if int(profile.get("character_id") or 0) == character_id
            ),
            None,
        )

    @staticmethod
    def _materialize_variant(
        current: BattleAnalysisSnapshot,
        loaded_variant: BattleAnalysisSnapshot,
        *,
        progress_callback: BattleAnalysisProgressCallback | None,
    ) -> BattleAnalysisSnapshot:
        """Project observed hits onto a variant before using it as a baseline."""

        comparison = BattleBuildCounterfactualService.compare(
            original=current,
            candidate=loaded_variant,
            progress_callback=progress_callback,
        )
        return replace(
            BattleBuildTimelineProjectionService.project(
                loaded_variant,
                comparison,
            ),
            build_counterfactual=None,
        )

    @staticmethod
    def _materialize_current(
        current: BattleAnalysisSnapshot,
    ) -> BattleAnalysisSnapshot:
        """Use a draft's projected fixed axis as the next benefit baseline."""

        comparison = current.build_counterfactual
        if comparison is None:
            return current
        return replace(
            BattleBuildTimelineProjectionService.project(current, comparison),
            build_counterfactual=None,
        )

    @staticmethod
    def _core(profile: Mapping[str, Any]) -> dict[str, Any] | None:
        cores = [
            deepcopy(dict(row))
            for row in profile.get("equipment_override") or ()
            if isinstance(row, Mapping)
            and str(row.get("kind") or "").casefold() == "core"
        ]
        return cores[0] if len(cores) == 1 else None

    @staticmethod
    def _replace_profile(
        candidate: BattleMarginalCandidate,
        character_id: int,
        replacement_profile: Mapping[str, Any],
    ) -> BattleMarginalCandidate:
        profiles = tuple(
            deepcopy(dict(replacement_profile))
            if int(profile.get("character_id") or 0) == character_id
            else deepcopy(dict(profile))
            for profile in candidate.profiles
        )
        return replace(candidate, profiles=profiles)

    @staticmethod
    def _static_catalog(
        database_path: str | Path | None,
        *,
        level: float,
    ) -> tuple[dict[str, tuple[str, bool, float]], dict[str, str]]:
        if database_path is None:
            return {}, {}
        catalog: dict[str, tuple[str, bool, float]] = {}
        fork_names: dict[str, str] = {}
        with StaticGameDataDao(database_path) as dao:
            attributes = {
                str(row["attribute_id"]): row
                for row in dao.list_equipment_attributes()
            }
            for property_id in _CORE_MAIN_PROPERTIES:
                value = dao.evaluate_equipment_base_attribute_curve(
                    f"{property_id}_Core_ITEM_QUALITY_ORANGE",
                    level,
                )
                attribute = attributes.get(property_id)
                if value is None or attribute is None:
                    continue
                label = str(
                    attribute.get("filter_name_zh")
                    or attribute.get("display_name_zh")
                    or property_id
                )
                catalog[property_id] = (
                    label,
                    bool(attribute.get("show_percent")),
                    float(value),
                )
            fork_names = {
                str(row["fork_id"]): str(row.get("name_zh") or row["fork_id"])
                for row in dao.list_forks()
            }
        return catalog, fork_names

    @classmethod
    def _delta(
        cls,
        comparison: BattleBuildCounterfactual,
        role_scope: BattleMarginalBenefitRoleScope,
        *,
        team_endpoint_damage: float | None = None,
        role_endpoint_damage: float | None = None,
        team_percent_denominator: float | None = None,
        role_percent_denominator: float | None = None,
    ) -> BattleMarginalDelta:
        role_rows = marginal_benefit_role_rows(comparison, role_scope)
        role_quantification = BattleBuildQuantificationService.aggregate(
            rows=role_rows,
            fixed_damage=0.0,
            fixed_unchanged=True,
        )
        role_quantification = with_awakening_gaps(
            role_quantification,
            awakening_gaps_for_character(
                comparison.quantification.gaps, role_scope.character_id,
            ),
        )
        role_baseline = sum(row.baseline_damage for row in role_rows)
        role_known_projection = (
            None
            if role_quantification.quantified_increment is None
            else role_baseline + role_quantification.quantified_increment
        )
        team_projected = cls._projection(
            comparison.quantification,
            (
                comparison.known_projection_damage
                if team_endpoint_damage is None
                else team_endpoint_damage
            ),
        )
        role_projected = cls._projection(
            role_quantification,
            (
                role_known_projection
                if role_endpoint_damage is None
                else role_endpoint_damage
            ),
        )
        team_gain = (
            None
            if team_projected is None
            else team_projected - comparison.baseline_damage
        )
        role_gain = (
            None if role_projected is None else role_projected - role_baseline
        )
        team_denominator = (
            comparison.baseline_damage
            if team_percent_denominator is None
            else team_percent_denominator
        )
        role_denominator = (
            role_baseline
            if role_percent_denominator is None
            else role_percent_denominator
        )
        gaps = tuple(dict.fromkeys(
            gap.explanation
            for quantification in (
                comparison.quantification,
                role_quantification,
            )
            for gap in quantification.gaps
        ))
        return BattleMarginalDelta(
            team_status=comparison.quantification.status,
            role_status=role_quantification.status,
            baseline_team_damage=float(comparison.baseline_damage),
            projected_team_damage=team_projected,
            team_gain_damage=team_gain,
            team_gain_percent=cls._gain_percent(team_gain, team_denominator),
            baseline_role_damage=role_baseline,
            projected_role_damage=role_projected,
            role_gain_damage=role_gain,
            role_gain_percent=cls._gain_percent(role_gain, role_denominator),
            team_coverage_percent=cls._coverage(comparison.quantification),
            role_coverage_percent=cls._coverage(role_quantification),
            gap_explanations=gaps,
        )

    @classmethod
    def _unchanged_delta(
        cls,
        analysis: BattleAnalysisSnapshot,
        role_scope: BattleMarginalBenefitRoleScope,
    ) -> BattleMarginalDelta:
        role_damage = cls._observed_panel_damage(analysis, role_scope)
        team_damage = float(analysis.effective_damage)
        return BattleMarginalDelta(
            team_status="not_applicable",
            role_status="not_applicable",
            baseline_team_damage=team_damage,
            projected_team_damage=team_damage,
            team_gain_damage=0.0,
            team_gain_percent=0.0,
            baseline_role_damage=role_damage,
            projected_role_damage=role_damage,
            role_gain_damage=0.0,
            role_gain_percent=0.0,
            team_coverage_percent=100.0,
            role_coverage_percent=100.0,
        )

    @staticmethod
    def _projection(
        quantification: BattleDamageQuantification,
        known_projection: float | None,
    ) -> float | None:
        if quantification.status == "unavailable":
            return None
        return None if known_projection is None else float(known_projection)

    @staticmethod
    def _coverage(quantification: BattleDamageQuantification) -> float:
        if quantification.status == "not_applicable":
            return 100.0
        if quantification.basis_damage <= 0.0:
            return 0.0
        return (
            quantification.fully_quantified_damage
            + quantification.partially_quantified_damage
        ) / quantification.basis_damage * 100.0

    @staticmethod
    def _gain_percent(gain: float | None, denominator: float) -> float | None:
        if gain is None or denominator <= 0.0:
            return None
        return gain / denominator * 100.0

    @staticmethod
    def _observed_panel_damage(
        analysis: BattleAnalysisSnapshot,
        role_scope: BattleMarginalBenefitRoleScope,
    ) -> float:
        return observed_marginal_benefit_role_damage(analysis, role_scope)

    @staticmethod
    def _closure(
        permanent: float | None,
        skill: float | None,
        comprehensive: float | None,
    ) -> float | None:
        if permanent is None or skill is None or comprehensive is None:
            return None
        return permanent + skill - comprehensive

    @staticmethod
    def _same_stat_value(first: float, second: float) -> bool:
        """Treat float32 capture noise as equal without merging quality tiers."""

        return isclose(float(first), float(second), rel_tol=1e-6, abs_tol=1e-6)


__all__ = ["BattleMarginalBenefitService"]
