# 把严格遭遇候选的逐击重放适配为原始公式残差输入并选出稳定胜者。
"""Candidate replay projection for automatic encounter residual fitting."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from src.domain.battle_encounter import BattleEncounterCandidate
from src.domain.battle_report import BattleAnalysisSnapshot
from src.services.battle_encounter_fit_service import (
    BattleEncounterFitCandidate,
    BattleEncounterFitPrediction,
    BattleEncounterFitSelection,
    BattleEncounterFitService,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_inferred_target_condition_service import (
    BattleInferredEncounter,
    BattleInferredTargetConditionService,
)
from src.services.battle_hit_replay_audit_service import (
    BattleHitReplayAuditService,
)


@dataclass(frozen=True, slots=True)
class BattleEncounterFitProjectionOutcome:
    inferred: BattleInferredEncounter
    analysis: BattleAnalysisSnapshot
    selection: BattleEncounterFitSelection


def _fit_group_id(hit, replay, analysis: BattleAnalysisSnapshot) -> str:
    source = (
        str(getattr(hit, "gameplay_effect_id", "") or "").strip()
        or str(getattr(hit, "damage_name", "") or "").strip()
        or str(getattr(hit, "ability_id", "") or "").strip()
        or str(getattr(hit, "skill_name", "") or "").strip()
        or str(getattr(hit, "event_id", "") or "").strip()
    )
    intervals = tuple(
        row
        for row in tuple(getattr(analysis, "buff_intervals", ()) or ())
        if str(getattr(row, "source_kind", "") or "")
        != "outer_realm_season_buff"
    )
    applied_intervals = (
        ()
        if not intervals
        else BattleBuffAttributeProjectionService.project_hit(
            hit,
            intervals,
        ).applied_interval_ids
    )
    state_factors = tuple(
        (
            str(getattr(factor, "factor_id", "") or ""),
            round(float(getattr(factor, "value", 0.0) or 0.0), 9),
        )
        for factor in tuple(getattr(replay, "factors", ()) or ())
        if str(getattr(factor, "factor_id", "") or "")
        in {"state_coefficient", "dot_final"}
    )
    return "|".join((
        str(getattr(hit, "character_id", "") or "system"),
        source,
        str(getattr(hit, "damage_attribute", "") or "unknown"),
        str(getattr(hit, "scope_half", "") or ""),
        str(getattr(hit, "target_id", "") or "unknown"),
        ",".join(applied_intervals) or "no-buff",
        repr(state_factors) if state_factors else "no-state-segment",
    ))


def _fit_candidate(
    candidate: BattleEncounterCandidate,
    analysis: BattleAnalysisSnapshot,
    group_ids: dict[str, str],
    excluded_event_ids: frozenset[str],
) -> BattleEncounterFitCandidate:
    hits = {row.event_id: row for row in analysis.hits}
    predictions = []
    for replay in analysis.hit_replays:
        if replay.event_id in excluded_event_ids:
            continue
        hit = hits.get(replay.event_id)
        if hit is None:
            continue
        raw_observed = getattr(hit, "raw_damage", None)
        observed = (
            float(raw_observed)
            if raw_observed is not None and float(raw_observed) > 0.0
            else float(replay.observed_damage)
        )
        predictions.append(BattleEncounterFitPrediction(
            event_id=replay.event_id,
            observed_damage=observed,
            non_critical_damage=replay.non_critical_damage,
            critical_damage=replay.critical_damage,
            expected_damage=replay.expected_damage,
            group_id=group_ids.get(replay.event_id, replay.event_id),
        ))
    return BattleEncounterFitCandidate(
        candidate_ref=candidate.environment_ref,
        predictions=tuple(predictions),
    )


class BattleEncounterFitProjectionService:
    """Replay every strict candidate, then promote the raw-residual winner."""

    @classmethod
    def select(
        cls,
        inferred: BattleInferredEncounter,
        *,
        project_candidate: Callable[
            [BattleEncounterCandidate], BattleAnalysisSnapshot
        ],
        group_analysis: BattleAnalysisSnapshot | None = None,
    ) -> BattleEncounterFitProjectionOutcome | None:
        matches = inferred.formula_matches
        if len(matches) < 2:
            return None
        analyses = {}
        for match in matches:
            candidate = match.candidate
            analysis = project_candidate(candidate)
            analyses[candidate.environment_ref] = analysis
        reference_analysis = analyses.get(inferred.environment_ref) or next(
            iter(analyses.values())
        )
        stable_analysis = group_analysis or reference_analysis
        stable_hits = {row.event_id: row for row in stable_analysis.hits}
        reference_hits = {
            row.event_id: row for row in reference_analysis.hits
        }
        group_ids = {
            replay.event_id: _fit_group_id(
                stable_hits.get(replay.event_id)
                or reference_hits[replay.event_id],
                replay,
                stable_analysis,
            )
            for replay in reference_analysis.hit_replays
            if replay.event_id in stable_hits or replay.event_id in reference_hits
        }
        excluded_event_ids = (
            BattleHitReplayAuditService.damage_attribution_conflict_ids(
                stable_analysis.hits
            )
        )
        candidates = [
            _fit_candidate(
                match.candidate,
                analyses[match.candidate.environment_ref],
                group_ids,
                excluded_event_ids,
            )
            for match in matches
        ]
        selection = BattleEncounterFitService.select(tuple(candidates))
        if (
            selection.selection_mode == "ambiguous_default"
            and inferred.environment_ref in analyses
            and selection.winner_ref != inferred.environment_ref
        ):
            selection = replace(
                selection,
                winner_ref=inferred.environment_ref,
                alternatives=tuple(
                    row.candidate.environment_ref
                    for row in matches
                    if row.candidate.environment_ref != inferred.environment_ref
                ),
                audit_summary=(
                    selection.audit_summary
                    + f" 残差完全一致，沿用硬证据默认 {inferred.environment_ref}。"
                ),
            )
        selected_analysis = analyses[selection.winner_ref]
        selected_score = next(
            row.robust_score
            for row in selection.scores
            if row.candidate_ref == selection.winner_ref
        )
        score_audit = "、".join(
            f"{row.candidate_ref}={row.robust_score:.6f}"
            f"（{row.used_hit_count}击/{row.used_group_count}组，"
            f"排除{row.excluded_hit_count}击）"
            for row in selection.scores
        )
        refined = BattleInferredTargetConditionService.select_residual_candidate(
            inferred,
            environment_ref=selection.winner_ref,
            confidence=selection.confidence,
            selection_mode=selection.selection_mode,
            score=selected_score,
            score_gap=selection.score_gap,
            audit_basis=(
                f"残差裁决模式 {selection.selection_mode}；"
                f"{selection.audit_summary} 候选鲁棒损失：{score_audit}；"
                f"算法 {selection.algorithm_version}。"
            ),
        )
        return BattleEncounterFitProjectionOutcome(
            inferred=refined,
            analysis=selected_analysis,
            selection=selection,
        )
