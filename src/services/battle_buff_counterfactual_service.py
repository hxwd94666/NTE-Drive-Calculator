# 在固定真实逐击轴上逐个移除 Buff，计算选定时段的独立伤害收益。
"""Per-Buff removal counterfactuals calibrated to observed battle damage."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.domain.battle_buff_counterfactual import (
    BattleBuffCounterfactualResult,
    BattleDamageCoverage,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitBuffProjection,
    BattleInferredBuffInterval,
    BattleSkillDamageEvidence,
)
from src.services.battle_buff_counterfactual_batch_executor import (
    BattleBuffCounterfactualBatchExecutor,
)
from src.services.battle_buff_counterfactual_plan_service import (
    BattleBuffCounterfactualPlanService,
    battle_buff_counterfactual_key,
)
from src.services.battle_buff_interval_index import BattleBuffIntervalIndex
from src.services.battle_buff_counterfactual_projection_support import (
    HitProjection,
    aggregate_quantification,
    beneficiary_result,
    vital_projections,
)
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.battle_selected_hit_replay_context import (
    PreparedReplayAuditInputs,
)
from src.services.battle_topple_hit_replay_service import BattleToppleCharacterConfig
from src.services.battle_analysis_progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_hit_buff_projection_cache import (
    BattleHitBuffProjectionCache,
)


BUFF_COUNTERFACTUAL_MODEL_VERSION = "battle-buff-counterfactual-v10"
_CONFIDENCE_ORDER = {"未解析": 0, "低": 1, "中": 2, "高": 3}


def _coverage_seconds(
    intervals: Sequence[BattleInferredBuffInterval],
    *,
    start_us: int,
    end_us: int,
) -> float:
    clipped = sorted(
        (
            max(start_us, row.start_us),
            min(end_us, row.end_us),
        )
        for row in intervals
        if min(end_us, row.end_us) > max(start_us, row.start_us)
    )
    if not clipped:
        return 0.0
    total = 0
    current_start, current_end = clipped[0]
    for next_start, next_end in clipped[1:]:
        if next_start <= current_end:
            current_end = max(current_end, next_end)
            continue
        total += current_end - current_start
        current_start, current_end = next_start, next_end
    total += current_end - current_start
    return total / 1_000_000.0


def _minimum_confidence(
    intervals: Sequence[BattleInferredBuffInterval],
) -> str:
    values = (
        value
        for row in intervals
        for value in (row.state_confidence, row.value_confidence)
    )
    return min(
        (value if value in _CONFIDENCE_ORDER else "低" for value in values),
        key=_CONFIDENCE_ORDER.__getitem__,
        default="未解析",
    )


def _damage_coverage(
    active_hits: Sequence[BattleAnalysisHit],
    intervals: Sequence[BattleInferredBuffInterval],
    *,
    basis_damage: float,
) -> BattleDamageCoverage:
    basis = max(0.0, float(basis_damage))
    scope = intervals[0].target_scope if intervals else "unknown"
    covered = 0.0
    unresolved = 0.0
    for hit in active_hits:
        damage = max(0.0, float(hit.damage))
        if scope == "unknown" or (
            scope == "team_others" and hit.character_id is None
        ):
            unresolved += damage
        else:
            covered += damage
    confirmed = min(basis, covered)
    unresolved = min(max(0.0, basis - confirmed), unresolved)
    return BattleDamageCoverage(
        basis_damage=basis,
        covered_damage=confirmed,
        unresolved_damage=unresolved,
    )


class BattleBuffCounterfactualService:
    """Measure each Buff independently by removing all its inferred intervals."""

    @classmethod
    def calculate(
        cls,
        analysis: BattleAnalysisSnapshot,
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        *,
        topple_character_configs: (
            Mapping[int, BattleToppleCharacterConfig] | None
        ) = None,
        progress_callback: BattleAnalysisProgressCallback | None = None,
        interval_index: BattleBuffIntervalIndex | None = None,
        original_projection_by_event: (
            Mapping[str, BattleHitBuffProjection] | None
        ) = None,
    ) -> tuple[BattleBuffCounterfactualResult, ...]:
        if not analysis.buff_intervals:
            return ()
        outgoing_hits = tuple(
            hit for hit in analysis.hits if hit.direction == "outgoing"
        )
        baseline_replays = analysis.hit_replays or BattleHitReplayService.replay(
            analysis,
            skill_evidence,
            topple_character_configs=topple_character_configs,
        )
        audit_inputs = PreparedReplayAuditInputs.prepare(
            analysis,
            skill_evidence,
            baseline_replays,
        )
        if interval_index is None:
            interval_index = BattleBuffIntervalIndex(analysis.buff_intervals)
        group_plans = BattleBuffCounterfactualPlanService.prepare(
            outgoing_hits,
            analysis.buff_intervals,
            interval_index,
        )
        active_hits_by_event = {
            hit.event_id: hit
            for plan in group_plans
            for hit in plan.active_hits
        }
        projection_cache = BattleHitBuffProjectionCache(interval_index)
        prepared_projections = dict(original_projection_by_event or {})
        prepared_projections.update({
            event_id: projection_cache.project(hit)
            for event_id, hit in active_hits_by_event.items()
            if event_id not in prepared_projections
        })
        baseline_hit_damage_by_event = {
            hit.event_id: float(hit.damage) for hit in outgoing_hits
        }
        baseline_hit_damage = sum(baseline_hit_damage_by_event.values())
        baseline_hit_damage_by_character: dict[int, float] = {}
        character_names: dict[int, str] = {}
        for hit in outgoing_hits:
            if hit.character_id is None or int(hit.character_id) <= 0:
                continue
            character_id = int(hit.character_id)
            baseline_hit_damage_by_character[character_id] = (
                baseline_hit_damage_by_character.get(character_id, 0.0)
                + float(hit.damage)
            )
            character_names[character_id] = hit.character_name
        character_names.update({
            int(event.source_character_id): event.source_character_name
            for event in analysis.max_hp_events
            if event.source_character_id is not None
            and int(event.source_character_id) > 0
        })
        total_groups = len(group_plans)
        report_battle_analysis_progress(
            progress_callback,
            phase="buff_counterfactual",
            message="正在逐组计算 Buff 移除反事实…",
            completed=0,
            total=total_groups,
        )
        results = []
        for ordinal, plan in enumerate(group_plans, start=1):
            results.append(cls._calculate_group(
                analysis=analysis,
                outgoing_hits=outgoing_hits,
                active_hits=plan.active_hits,
                group_key=plan.group_key,
                group_intervals=plan.intervals,
                interval_index=interval_index,
                original_projection_by_event=prepared_projections,
                audit_inputs=audit_inputs,
                baseline_hit_damage=baseline_hit_damage,
                baseline_hit_damage_by_event=baseline_hit_damage_by_event,
                baseline_hit_damage_by_character=(
                    baseline_hit_damage_by_character
                ),
                character_names=character_names,
                skill_evidence=skill_evidence,
                topple_character_configs=topple_character_configs,
                progress_callback=progress_callback,
            ))
            report_battle_analysis_progress(
                progress_callback,
                phase="buff_counterfactual",
                message="正在逐组计算 Buff 移除反事实…",
                completed=ordinal,
                total=total_groups,
            )
        return tuple(results)

    @classmethod
    def _calculate_group(
        cls,
        *,
        analysis: BattleAnalysisSnapshot,
        outgoing_hits: Sequence[BattleAnalysisHit],
        active_hits: Sequence[BattleAnalysisHit],
        group_key: str,
        group_intervals: tuple[BattleInferredBuffInterval, ...],
        interval_index: BattleBuffIntervalIndex,
        original_projection_by_event: Mapping[str, BattleHitBuffProjection],
        audit_inputs: PreparedReplayAuditInputs,
        baseline_hit_damage: float,
        baseline_hit_damage_by_event: Mapping[str, float],
        baseline_hit_damage_by_character: Mapping[int, float],
        character_names: Mapping[int, str],
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        topple_character_configs: (
            Mapping[int, BattleToppleCharacterConfig] | None
        ),
        progress_callback: BattleAnalysisProgressCallback | None,
    ) -> BattleBuffCounterfactualResult:
        first = group_intervals[0]
        active_ratios = BattleBuffCounterfactualBatchExecutor.calculate_ratios(
            analysis=analysis,
            outgoing_hits=outgoing_hits,
            active_hits=active_hits,
            group_intervals=group_intervals,
            interval_index=interval_index,
            original_projection_by_event=original_projection_by_event,
            audit_inputs=audit_inputs,
            skill_evidence=skill_evidence,
            topple_character_configs=topple_character_configs,
            progress_callback=progress_callback,
        )

        hit_projections: dict[str, HitProjection] = {}
        for hit in active_hits:
            ratio = active_ratios[hit.event_id]
            quantified_ratio = ratio.quantified_ratio
            predicted = float(hit.damage) * (
                quantified_ratio if quantified_ratio is not None else 1.0
            )
            hit_projections[hit.event_id] = HitProjection(
                hit=hit,
                predicted_damage=predicted,
                quantification=ratio,
            )

        quantified_ids = {
            event_id
            for event_id, row in hit_projections.items()
            if row.quantification.status in {"complete", "partial"}
        }

        active_baseline_hit_damage = sum(
            float(hit.damage) for hit in active_hits
        )
        without_hit_damage = baseline_hit_damage + sum(
            row.predicted_damage - float(row.hit.damage)
            for row in hit_projections.values()
        )
        baseline_vital_damage = sum(
            max(0.0, float(event.effective_hp_loss))
            for event in analysis.max_hp_events
        )
        projected_vitals = vital_projections(
            analysis,
            hit_projections,
            baseline_hit_damage_by_event,
        )
        without_vital_damage = sum(
            row.predicted_damage for row in projected_vitals
        )
        derived_baseline = (
            float(analysis.effective_damage)
            if analysis.effective_damage > 0
            else baseline_hit_damage + baseline_vital_damage
        )
        fixed_derived_damage = max(
            0.0,
            derived_baseline - baseline_hit_damage - baseline_vital_damage,
        )
        without_damage = (
            without_hit_damage + without_vital_damage + fixed_derived_damage
        )
        known_damage_gain = derived_baseline - without_damage
        quantification = aggregate_quantification(
            hit_projections=tuple(hit_projections.values()),
            vital_projections=projected_vitals,
            fixed_derived_damage=fixed_derived_damage,
            proven_unchanged_hit_damage=max(
                0.0,
                baseline_hit_damage - active_baseline_hit_damage,
            ),
            quantified_increment=known_damage_gain,
        )
        quantified_damage_gain = (
            None
            if quantification.status == "unavailable"
            else known_damage_gain
        )
        quantified_gain_percent = (
            quantified_damage_gain / without_damage * 100.0
            if quantified_damage_gain is not None and without_damage > 0
            else (0.0 if quantified_damage_gain is not None else None)
        )
        full_available = quantification.status in {"complete", "not_applicable"}
        without_buff_damage = without_damage if full_available else None
        damage_gain = known_damage_gain if full_available else None
        gain_percent = quantified_gain_percent if full_available else None
        beneficiary_ids = {
            int(hit.character_id)
            for hit in active_hits
            if hit.character_id is not None and int(hit.character_id) > 0
        } | {
            int(row.character_id)
            for row in projected_vitals
            if row.character_id is not None
            and int(row.character_id) > 0
            and row.status in {"complete", "partial", "unavailable"}
        }
        beneficiaries = tuple(
            beneficiary_result(
                character_id=character_id,
                character_name=character_names.get(
                    character_id,
                    f"角色 {character_id}",
                ),
                hit_projections=tuple(hit_projections.values()),
                baseline_hit_damage=baseline_hit_damage_by_character.get(
                    character_id,
                    0.0,
                ),
                vital_projections=tuple(
                    row
                    for row in projected_vitals
                    if row.character_id == character_id
                ),
                team_without_quantified_effect_damage=(
                    without_damage
                    if quantification.status != "unavailable"
                    else None
                ),
                team_without_buff_damage=without_buff_damage,
            )
            for character_id in sorted(beneficiary_ids)
        )
        quantified_attributed_gain = sum(
            row.quantified_damage_gain or 0.0 for row in beneficiaries
        )
        quantified_unattributed_damage_gain = (
            None
            if quantified_damage_gain is None
            else quantified_damage_gain - quantified_attributed_gain
        )
        if (
            quantified_unattributed_damage_gain is not None
            and abs(quantified_unattributed_damage_gain) < 1e-9
        ):
            quantified_unattributed_damage_gain = 0.0
        attributed_gain = sum(row.damage_gain or 0.0 for row in beneficiaries)
        unattributed_damage_gain = (
            None if damage_gain is None else damage_gain - attributed_gain
        )
        if unattributed_damage_gain is not None and abs(unattributed_damage_gain) < 1e-9:
            unattributed_damage_gain = 0.0
        if not active_hits:
            method = "not_covered"
            confidence = "未解析"
            explanation = "该 Buff 在当前选定时段没有覆盖对敌逐击。"
        elif quantification.status == "unavailable":
            method = "component_ratio_unavailable"
            confidence = "低"
            explanation = (
                "该 Buff 的相关变化缺少必要公式输入；原轴仍完整保留，"
                "未量化收益不记为 0。"
            )
        elif quantification.status == "partial":
            method = "component_ratio_partial"
            confidence = "低"
            explanation = (
                "以真实逐击为锚，只缩放可安全相消或公式已完整的变化；"
                "已量化分量不代表完整 Buff 收益或收益下限。"
            )
        elif quantification.status == "not_applicable":
            method = "not_applicable"
            confidence = _minimum_confidence(group_intervals)
            explanation = "已证明该 Buff 在当前时段不改变任何相关对敌逐击。"
        else:
            method = "observed_axis_remove_replay"
            confidence = _minimum_confidence(group_intervals)
            explanation = (
                "以真实逐击伤害为锚，按移除该 Buff 前后的公式期望比逐击缩放；"
                "未量化逐击保持原值。"
            )
        return BattleBuffCounterfactualResult(
            buff_key=group_key,
            source_character_id=first.source_character_id,
            source_character_name=first.source_character_name,
            buff_name=first.buff_name,
            buff_asset_path=first.buff_asset_path,
            source_effect_definition_id=first.source_effect_definition_id,
            target_scope=first.target_scope,
            interval_count=len(group_intervals),
            coverage_seconds=_coverage_seconds(
                group_intervals,
                start_us=analysis.range_start_us,
                end_us=analysis.range_end_us,
            ),
            affected_hits=len(active_hits),
            quantified_hits=len(quantified_ids),
            baseline_damage=derived_baseline,
            without_quantified_effect_damage=(
                without_damage
                if quantification.status != "unavailable"
                else None
            ),
            quantified_damage_gain=quantified_damage_gain,
            quantified_gain_percent=quantified_gain_percent,
            without_buff_damage=without_buff_damage,
            damage_gain=damage_gain,
            gain_percent=gain_percent,
            confidence=confidence,
            method=method,
            explanation=explanation,
            quantification=quantification,
            beneficiaries=beneficiaries,
            quantified_unattributed_damage_gain=quantified_unattributed_damage_gain,
            unattributed_damage_gain=unattributed_damage_gain,
            damage_coverage=_damage_coverage(
                active_hits,
                group_intervals,
                basis_damage=baseline_hit_damage,
            ),
        )
