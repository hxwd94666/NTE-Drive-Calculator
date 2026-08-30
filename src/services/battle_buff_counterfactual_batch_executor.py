# 对一个 Buff 移除候选批量计算安全的逐击公式比。
"""Representative formula execution for one Buff counterfactual group."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleQuantificationGap,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_buff_interval_index import (
    BattleBuffIntervalIndex,
    BattleBuffIntervalQuery,
)
from src.services.battle_direct_formula_batch_service import (
    BattleDirectFormulaBatchService,
)
from src.services.battle_hit_counterfactual_ratio_service import (
    BattleHitCounterfactualRatioService,
)
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.battle_selected_hit_replay_context import (
    PreparedReplayAuditContext,
    PreparedReplayAuditInputs,
)
from src.services.battle_topple_hit_replay_service import (
    BattleToppleCharacterConfig,
)
from src.services.battle_analysis_progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_hit_buff_projection_cache import (
    BattleHitBuffProjectionCache,
)


def _formula_character_id(
    hit: BattleAnalysisHit,
    evidence: BattleSkillDamageEvidence | None,
) -> int | None:
    return (
        evidence.source_character_id
        if evidence is not None and evidence.source_character_id is not None
        else hit.character_id
    )


def _formula_hit(
    hit: BattleAnalysisHit,
    formula_character_id: int | None,
) -> BattleAnalysisHit:
    return (
        hit
        if formula_character_id == hit.character_id
        else replace(hit, character_id=formula_character_id)
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
    if ratio.status != "not_applicable" and (applied or not unresolved):
        return ratio
    beneficiary_unknown = any("逐击角色未知" in reason for reason in unresolved)
    lacks_formula = not any(row.modifiers for row in group_intervals)
    if not applied and not unresolved and not lacks_formula:
        return ratio
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
    ) -> dict[str, BattleCounterfactualRatio]:
        if not active_hits:
            return {}
        active_ids = frozenset(hit.event_id for hit in active_hits)
        removed_interval_ids = frozenset(
            interval.interval_id for interval in group_intervals
        )
        without_interval_index = interval_index.excluding(removed_interval_ids)
        group_interval_index = BattleBuffIntervalIndex(group_intervals)
        candidate_projection_cache = BattleHitBuffProjectionCache(
            without_interval_index
        )
        group_projection_cache = BattleHitBuffProjectionCache(
            group_interval_index
        )

        formula_character_id_by_event = {
            hit.event_id: _formula_character_id(
                hit,
                audit_inputs.evidence_by_event.get(hit.event_id),
            )
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
            formula_hit = _formula_hit(
                hit,
                formula_character_id_by_event[event_id],
            )
            candidate_formula_projection_by_event[event_id] = (
                candidate_projection_by_event[event_id]
                if formula_hit is hit
                else candidate_projection_cache.project(formula_hit)
            )
        group_projection_by_event = {
            hit.event_id: group_projection_cache.project(hit)
            for hit in _progressive_hits(
                active_hits,
                progress_callback,
                phase="buff_counterfactual_prepare",
                message="正在核对当前 Buff 组的逐击覆盖范围…",
            )
        }
        target_condition_by_event = {
            event_id: target_condition
            for event_id, target_condition
            in audit_inputs.target_condition_by_event.items()
            if event_id in active_ids
        }
        if not any(interval.modifiers for interval in group_intervals):
            return {
                hit.event_id: _resolve_projection_gap(
                    cls._compare(
                        hit,
                        None,
                        formula_character_id_by_event=(
                            formula_character_id_by_event
                        ),
                        original_projection_by_event=(
                            original_projection_by_event
                        ),
                        candidate_projection_by_event=(
                            candidate_projection_by_event
                        ),
                        target_condition_by_event=target_condition_by_event,
                        audit_inputs=audit_inputs,
                    ),
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
                audit_inputs=audit_inputs,
                audit_context=None,
                interval_index=without_interval_index,
                projection_by_event=None,
                progress_callback=progress_callback,
            )
            raw_ratios = {
                hit.event_id: cls._compare(
                    hit,
                    candidate_by_event.get(hit.event_id),
                    formula_character_id_by_event=formula_character_id_by_event,
                    original_projection_by_event=original_projection_by_event,
                    candidate_projection_by_event=candidate_projection_by_event,
                    target_condition_by_event=target_condition_by_event,
                    audit_inputs=audit_inputs,
                )
                for hit in _progressive_hits(
                    active_hits,
                    progress_callback,
                    phase="buff_counterfactual_compare",
                    message="正在比较状态机制的逐击反事实…",
                )
            }
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
                audit_inputs=audit_inputs,
                audit_context=audit_inputs.select(representative_ids),
                interval_index=without_interval_index,
                projection_by_event=candidate_formula_projection_by_event,
                progress_callback=progress_callback,
            )
            raw_ratios: dict[str, BattleCounterfactualRatio] = {}
            fallback_hits: list[BattleAnalysisHit] = []
            for ordinal, batch in enumerate(batches, start=1):
                if ordinal == 1 or (ordinal - 1) % 64 == 0:
                    report_battle_analysis_progress(
                        progress_callback,
                        phase="buff_counterfactual_compare",
                        message="正在比较去重后的代表击反事实…",
                    )
                representative = batch.representative
                representative_ratio = cls._compare(
                    representative,
                    candidate_by_event.get(representative.event_id),
                    formula_character_id_by_event=formula_character_id_by_event,
                    original_projection_by_event=original_projection_by_event,
                    candidate_projection_by_event=candidate_projection_by_event,
                    target_condition_by_event=target_condition_by_event,
                    audit_inputs=audit_inputs,
                )
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
                    audit_inputs=audit_inputs,
                    audit_context=audit_inputs.select(fallback_ids),
                    interval_index=without_interval_index,
                    projection_by_event=candidate_formula_projection_by_event,
                    progress_callback=progress_callback,
                )
                for hit in _progressive_hits(
                    fallback_hits,
                    progress_callback,
                    phase="buff_counterfactual_compare",
                    message="正在比较未能共享结果的逐击反事实…",
                ):
                    raw_ratios[hit.event_id] = cls._compare(
                        hit,
                        fallback_candidates.get(hit.event_id),
                        formula_character_id_by_event=(
                            formula_character_id_by_event
                        ),
                        original_projection_by_event=(
                            original_projection_by_event
                        ),
                        candidate_projection_by_event=(
                            candidate_projection_by_event
                        ),
                        target_condition_by_event=target_condition_by_event,
                        audit_inputs=audit_inputs,
                    )

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
    ) -> dict[str, BattleHitReplayResult]:
        results = BattleHitReplayService.replay(
            analysis,
            skill_evidence,
            topple_character_configs=topple_character_configs,
            prepared_audit_context=audit_context,
            prepared_audit_inputs=audit_inputs,
            buff_interval_index=interval_index,
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
    def _compare(
        hit: BattleAnalysisHit,
        candidate_replay: BattleHitReplayResult | None,
        *,
        formula_character_id_by_event: Mapping[str, int | None],
        original_projection_by_event: Mapping[str, BattleHitBuffProjection],
        candidate_projection_by_event: Mapping[str, BattleHitBuffProjection],
        target_condition_by_event: Mapping[str, BattleTargetCondition | None],
        audit_inputs: PreparedReplayAuditInputs,
    ) -> BattleCounterfactualRatio:
        event_id = hit.event_id
        formula_character_id = formula_character_id_by_event[event_id]
        baseline = (
            None
            if formula_character_id is None
            else audit_inputs.baselines_by_character.get(formula_character_id)
        )
        return BattleHitCounterfactualRatioService.compare(
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
