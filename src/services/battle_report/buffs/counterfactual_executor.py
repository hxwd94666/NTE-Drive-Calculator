# 对一个 Buff 移除候选批量计算安全的逐击公式比。
"""Representative formula execution for one Buff counterfactual group."""

from __future__ import annotations

from src.services.battle_report.damage.weave_source import BattleWeaveSourceIndex

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleQuantificationGap,
)
from src.domain.native_analysis import DirectFormulaBackend, available_battle_compute
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.buffs.candidate_projection import PreparedBuffCandidateProjection
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_report.buffs.interval_index import (
    BattleBuffIntervalIndex,
    BattleBuffIntervalQuery,
)
from src.services.battle_report.damage.direct_formula_batch import (
    BattleDirectFormulaBatchService,
)
from src.services.battle_report.native.counterfactual import compare_counterfactual_batch
from src.services.battle_report.replay.service import BattleHitReplayService
from src.services.battle_report.replay.selected_context import (
    PreparedReplayAuditContext,
    PreparedReplayAuditInputs,
)
from src.services.battle_report.replay.topple import (
    BattleToppleCharacterConfig,
)
from src.services.battle_report.analysis.progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_report.replay.buff_cache import (
    BattleHitBuffProjectionCache,
)


def _progressive_hits(
    hits: Sequence[BattleAnalysisHit],
    callback: BattleAnalysisProgressCallback | None,
    *,
    phase: str,
    message: str,
) -> Iterator[BattleAnalysisHit]:
    for ordinal, hit in enumerate(hits, start=1):
        if ordinal == 1 or (ordinal - 1) % 64 == 0:
            report_battle_analysis_progress(
                callback,
                phase=phase,
                message=message,
            )
        yield hit


def _resolve_projection_gap(
    ratio: BattleCounterfactualRatio,
    *,
    group_projection: BattleHitBuffProjection,
    group_intervals: Sequence[BattleInferredBuffInterval],
) -> BattleCounterfactualRatio:
    applied = any(
        decision.status == "applied"
        for decision in group_projection.decisions
    )
    unresolved = tuple(
        reason
        for decision in group_projection.decisions
        if decision.status == "unresolved"
        for reason in decision.reasons
    )
    if ratio.status != "not_applicable" and applied:
        return ratio
    beneficiary_unknown = any("逐击角色未知" in reason for reason in unresolved)
    lacks_formula = not any(row.modifiers for row in group_intervals)
    if not applied and not unresolved and not lacks_formula:
        return BattleCounterfactualRatio.not_applicable(
            method="buff_projection_not_applied",
            dependency_scope=ratio.dependency_scope,
            cancelled_dimension_ids=ratio.cancelled_dimension_ids,
            explanation="正式 Buff 修正已确认不适用于本击，移除前后倍率相同。",
        )
    explanation = (
        "逐击角色未知，无法确认该击是否属于来源角色之外的队友。"
        if beneficiary_unknown
        else (
            "Buff 修正已投影，但当前逐击公式尚未映射该变化。"
            if applied
            else (
                "Buff 存在未解析的数值或 Calculation，不能证明本击不受影响。"
                if unresolved
                else "Buff 缺少可计算的属性修正，不能把收益记为 0。"
            )
        )
    )
    gap = BattleQuantificationGap(
        code=(
            "team_others_beneficiary_unknown"
            if beneficiary_unknown
            else "formula_family_unsupported"
        ),
        dimension_id="buff_projection",
        dependency_scope="mechanic_specific",
        property_ids=tuple(sorted({
            modifier.property_id
            for row in group_intervals
            for modifier in row.modifiers
        })),
        explanation=explanation,
    )
    return BattleCounterfactualRatio.unavailable(
        method="buff_projection_unavailable",
        confidence="低",
        dependency_scope="mechanic_specific",
        cancelled_dimension_ids=ratio.cancelled_dimension_ids,
        gaps=(gap,),
        explanation=explanation,
    )


class BattleBuffCounterfactualBatchExecutor:
    """Return one safe formula ratio for every active observed hit."""

    @classmethod
    def calculate_ratios(
        cls,
        *,
        analysis: BattleAnalysisSnapshot,
        outgoing_hits: Sequence[BattleAnalysisHit],
        active_hits: Sequence[BattleAnalysisHit],
        group_intervals: tuple[BattleInferredBuffInterval, ...],
        interval_index: BattleBuffIntervalIndex,
        original_projection_by_event: Mapping[str, BattleHitBuffProjection],
        audit_inputs: PreparedReplayAuditInputs,
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        topple_character_configs: (
            Mapping[int, BattleToppleCharacterConfig] | None
        ),
        progress_callback: BattleAnalysisProgressCallback | None = None,
        direct_formula_backend: DirectFormulaBackend | None = None,
        projection_memo: BattleBuffProjectionMemo | None = None,
        prepared_candidate: PreparedBuffCandidateProjection | None = None,
    ) -> dict[str, BattleCounterfactualRatio]:
        if not active_hits:
            return {}
        if projection_memo is None:
            projection_memo = BattleBuffProjectionMemo()
        projection_memo.bind_backend(direct_formula_backend, progress_callback)
        active_ids = frozenset(hit.event_id for hit in active_hits)
        if prepared_candidate is None:
            prepared_candidate = PreparedBuffCandidateProjection.create(
                interval_index=interval_index, group_intervals=group_intervals,
                active_hits=active_hits, memo=projection_memo, evidence_by_event=audit_inputs.evidence_by_event,
                weave_sources=BattleWeaveSourceIndex(outgoing_hits),
            )
            prepared_candidate.candidate_cache.prepare((*active_hits, *prepared_candidate.formula_hits.values()))
            prepared_candidate.group_cache.prepare(prepared_candidate.consumer_hits.values())
        prepared_candidate.require_inputs(interval_index, group_intervals, active_hits, projection_memo)
        without_interval_index = prepared_candidate.without_index
        candidate_projection_cache = prepared_candidate.candidate_cache
        group_projection_cache = prepared_candidate.group_cache
        formula_hits = prepared_candidate.formula_hits
        consumer_hits = prepared_candidate.consumer_hits

        formula_character_id_by_event = {
            hit.event_id: formula_hits[hit.event_id].character_id
            for hit in _progressive_hits(
                active_hits,
                progress_callback,
                phase="buff_counterfactual_prepare",
                message="正在整理当前 Buff 组的逐击公式身份…",
            )
        }
        candidate_projection_by_event = {
            hit.event_id: candidate_projection_cache.project(hit)
            for hit in _progressive_hits(
                active_hits,
                progress_callback,
                phase="buff_counterfactual_prepare",
                message="正在投影移除 Buff 后的逐击属性…",
            )
        }
        candidate_formula_projection_by_event = {}
        for hit in _progressive_hits(
            active_hits,
            progress_callback,
            phase="buff_counterfactual_prepare",
            message="正在合并当前 Buff 组的公式投影…",
        ):
            event_id = hit.event_id
            formula_hit = formula_hits[event_id]
            candidate_formula_projection_by_event[event_id] = (
                candidate_projection_by_event[event_id]
                if formula_hit is hit
                else candidate_projection_cache.project(formula_hit)
            )
        group_projection_by_event = {}
        for hit in _progressive_hits(
                active_hits,
                progress_callback,
                phase="buff_counterfactual_prepare",
                message="正在核对当前 Buff 组的逐击覆盖范围…",
        ):
            group_projection_by_event[hit.event_id] = (
                group_projection_cache.project(consumer_hits[hit.event_id])
            )
        target_condition_by_event = {
            event_id: target_condition
            for event_id, target_condition
            in audit_inputs.target_condition_by_event.items()
            if event_id in active_ids
        }

        def compare_hits(hits, candidates):
            jobs = [
                cls._comparison_input(
                    hit, candidates.get(hit.event_id),
                    formula_character_id_by_event=formula_character_id_by_event,
                    original_projection_by_event=original_projection_by_event,
                    candidate_projection_by_event=candidate_formula_projection_by_event,
                    target_condition_by_event=target_condition_by_event, audit_inputs=audit_inputs,
                )
                for hit in _progressive_hits(hits, progress_callback, phase="buff_counterfactual_compare",
                                             message="正在准备整批逐击反事实比较…")
            ]
            ratios = compare_counterfactual_batch(
                jobs, backend=available_battle_compute(direct_formula_backend),
                checkpoint=lambda: report_battle_analysis_progress(
                    progress_callback, phase="buff_counterfactual_compare", message="正在批量比较逐击反事实…",
                ),
            )
            return {hit.event_id: ratio for hit, ratio in zip(hits, ratios, strict=True)}

        if not any(interval.modifiers for interval in group_intervals):
            raw_ratios = compare_hits(active_hits, {})
            return {
                hit.event_id: _resolve_projection_gap(
                    raw_ratios[hit.event_id],
                    group_projection=group_projection_by_event[hit.event_id],
                    group_intervals=group_intervals,
                )
                for hit in _progressive_hits(
                    active_hits,
                    progress_callback,
                    phase="buff_counterfactual_compare",
                    message="正在标记缺少正式修正的逐击证据…",
                )
            }
        full_context = audit_inputs.select(active_ids)

        if full_context.requires_full_axis:
            without_analysis = replace(
                analysis,
                buff_intervals=without_interval_index.intervals,
                hit_replays=(),
                buff_counterfactuals=(),
            )
            candidate_by_event = cls._replay(
                analysis=replace(without_analysis, hits=tuple(outgoing_hits)),
                replay_hits=outgoing_hits,
                skill_evidence=skill_evidence,
                topple_character_configs=topple_character_configs,
                direct_formula_backend=direct_formula_backend,
                audit_inputs=audit_inputs,
                buff_projection_cache=candidate_projection_cache,
                audit_context=None,
                interval_index=without_interval_index,
                projection_by_event=None,
                progress_callback=progress_callback,
            )
            raw_ratios = compare_hits(active_hits, candidate_by_event)
        else:
            batches = BattleDirectFormulaBatchService.plan(
                active_hits,
                formula_character_id_by_event=formula_character_id_by_event,
                baselines=audit_inputs.baselines_by_character,
                evidence_by_event=audit_inputs.evidence_by_event,
                original_replay_by_event=audit_inputs.baseline_replay_by_event,
                original_projection_by_event=original_projection_by_event,
                candidate_projection_by_event=candidate_projection_by_event,
                candidate_formula_projection_by_event=(
                    candidate_formula_projection_by_event
                ),
                target_condition_by_event=target_condition_by_event,
                progress_callback=progress_callback,
            )
            representatives = tuple(batch.representative for batch in batches)
            representative_ids = frozenset(
                hit.event_id for hit in representatives
            )
            candidate_by_event = cls._replay(
                analysis=replace(
                    analysis,
                    hits=representatives,
                    hit_replays=(),
                    buff_counterfactuals=(),
                ),
                replay_hits=representatives,
                skill_evidence=skill_evidence,
                topple_character_configs=topple_character_configs,
                direct_formula_backend=direct_formula_backend,
                audit_inputs=audit_inputs,
                buff_projection_cache=candidate_projection_cache,
                audit_context=audit_inputs.select(representative_ids),
                interval_index=without_interval_index,
                projection_by_event=candidate_formula_projection_by_event,
                progress_callback=progress_callback,
            )
            raw_ratios: dict[str, BattleCounterfactualRatio] = {}
            fallback_hits: list[BattleAnalysisHit] = []
            representative_ratios = compare_hits(representatives, candidate_by_event)
            for ordinal, batch in enumerate(batches, start=1):
                if ordinal == 1 or (ordinal - 1) % 64 == 0:
                    report_battle_analysis_progress(
                        progress_callback,
                        phase="buff_counterfactual_compare",
                        message="正在比较去重后的代表击反事实…",
                    )
                representative = batch.representative
                representative_ratio = representative_ratios[representative.event_id]
                raw_ratios[representative.event_id] = representative_ratio
                if len(batch.members) == 1:
                    continue
                if BattleDirectFormulaBatchService.ratio_can_be_shared(
                    representative_ratio
                ):
                    for hit in _progressive_hits(
                        batch.members[1:],
                        progress_callback,
                        phase="buff_counterfactual_compare",
                        message="正在回填相同公式的逐击收益…",
                    ):
                        raw_ratios[hit.event_id] = representative_ratio
                else:
                    fallback_hits.extend(batch.members[1:])
            if fallback_hits:
                fallback_ids = frozenset(hit.event_id for hit in fallback_hits)
                fallback_candidates = cls._replay(
                    analysis=replace(
                        analysis,
                        hits=tuple(fallback_hits),
                        hit_replays=(),
                        buff_counterfactuals=(),
                    ),
                    replay_hits=fallback_hits,
                    skill_evidence=skill_evidence,
                    topple_character_configs=topple_character_configs,
                    direct_formula_backend=direct_formula_backend,
                    audit_inputs=audit_inputs,
                    buff_projection_cache=candidate_projection_cache,
                    audit_context=audit_inputs.select(fallback_ids),
                    interval_index=without_interval_index,
                    projection_by_event=candidate_formula_projection_by_event,
                    progress_callback=progress_callback,
                )
                raw_ratios.update(compare_hits(fallback_hits, fallback_candidates))

        return {
            hit.event_id: _resolve_projection_gap(
                raw_ratios[hit.event_id],
                group_projection=group_projection_by_event[hit.event_id],
                group_intervals=group_intervals,
            )
            for hit in _progressive_hits(
                active_hits,
                progress_callback,
                phase="buff_counterfactual_compare",
                message="正在汇总当前 Buff 组的逐击反事实…",
            )
        }

    @staticmethod
    def _replay(
        *,
        analysis: BattleAnalysisSnapshot,
        replay_hits: Sequence[BattleAnalysisHit],
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        topple_character_configs: (
            Mapping[int, BattleToppleCharacterConfig] | None
        ),
        audit_inputs: PreparedReplayAuditInputs,
        audit_context: PreparedReplayAuditContext | None,
        interval_index: BattleBuffIntervalQuery,
        projection_by_event: Mapping[str, BattleHitBuffProjection] | None,
        progress_callback: BattleAnalysisProgressCallback | None,
        direct_formula_backend: DirectFormulaBackend | None = None,
        buff_projection_cache: BattleHitBuffProjectionCache | None = None,
    ) -> dict[str, BattleHitReplayResult]:
        results = BattleHitReplayService.replay(
            analysis,
            skill_evidence,
            topple_character_configs=topple_character_configs,
            direct_formula_backend=direct_formula_backend,
            prepared_audit_context=audit_context,
            prepared_audit_inputs=audit_inputs,
            buff_interval_index=interval_index,
            buff_projection_cache=buff_projection_cache,
            projection_by_event=projection_by_event,
            progress_callback=progress_callback,
            progress_phase="buff_counterfactual_replay",
            progress_message="正在重放当前 Buff 组的固定轴逐击…",
        )
        expected_ids = {hit.event_id for hit in replay_hits}
        return {
            result.event_id: result
            for result in results
            if result.event_id in expected_ids
        }

    @staticmethod
    def _comparison_input(
        hit: BattleAnalysisHit,
        candidate_replay: BattleHitReplayResult | None,
        *,
        formula_character_id_by_event: Mapping[str, int | None],
        original_projection_by_event: Mapping[str, BattleHitBuffProjection],
        candidate_projection_by_event: Mapping[str, BattleHitBuffProjection],
        target_condition_by_event: Mapping[str, BattleTargetCondition | None],
        audit_inputs: PreparedReplayAuditInputs,
    ) -> dict:
        event_id = hit.event_id
        formula_character_id = formula_character_id_by_event[event_id]
        baseline = (
            None
            if formula_character_id is None
            else audit_inputs.baselines_by_character.get(formula_character_id)
        )
        return dict(
            hit=hit,
            original_baseline=baseline,
            candidate_baseline=baseline,
            original_projection=original_projection_by_event[event_id],
            candidate_projection=candidate_projection_by_event[event_id],
            skill_evidence=audit_inputs.evidence_by_event.get(event_id),
            original_replay=audit_inputs.baseline_replay_by_event.get(event_id),
            candidate_replay=candidate_replay,
            target_condition=target_condition_by_event.get(event_id),
        )


__all__ = ["BattleBuffCounterfactualBatchExecutor"]
