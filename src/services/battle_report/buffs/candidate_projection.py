# 在一次 Buff 反事实请求中联合准备所有正式候选的完整逐击属性投影。
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.domain.battle_report import BattleAnalysisHit, BattleInferredBuffInterval, BattleSkillDamageEvidence
from src.services.battle_report.analysis.progress import BattleAnalysisProgressCallback, report_battle_analysis_progress
from src.services.battle_report.buffs.counterfactual_plan import BattleBuffCounterfactualGroupPlan
from src.services.battle_report.buffs.interval_index import BattleBuffIntervalIndex, BattleBuffIntervalIndexView
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.replay.formula_projection import project_formula_hit
from src.services.battle_report.replay.buff_cache import BattleHitBuffProjectionCache
from src.services.battle_report.damage.weave_source import BattleWeaveSourceIndex


@dataclass(frozen=True, slots=True)
class PreparedBuffCandidateProjection:
    source_index: BattleBuffIntervalIndex
    group_intervals: tuple[BattleInferredBuffInterval, ...]
    active_hits: tuple[BattleAnalysisHit, ...]
    without_index: BattleBuffIntervalIndexView
    group_index: BattleBuffIntervalIndex
    candidate_cache: BattleHitBuffProjectionCache
    group_cache: BattleHitBuffProjectionCache
    formula_hits: Mapping[str, BattleAnalysisHit]
    consumer_hits: Mapping[str, BattleAnalysisHit]

    @classmethod
    def create(cls, *, interval_index: BattleBuffIntervalIndex,
               group_intervals: tuple[BattleInferredBuffInterval, ...],
               active_hits: Sequence[BattleAnalysisHit], memo: BattleBuffProjectionMemo,
               evidence_by_event: Mapping[str, BattleSkillDamageEvidence],
               formula_by_event: Mapping[str, BattleAnalysisHit] | None = None,
               weave_sources: BattleWeaveSourceIndex | None = None) -> PreparedBuffCandidateProjection:
        without_index = interval_index.excluding(frozenset(row.interval_id for row in group_intervals))
        group_index = BattleBuffIntervalIndex(group_intervals)
        formula_hits = {
            hit.event_id: (formula_by_event[hit.event_id] if formula_by_event is not None
                           else project_formula_hit(hit, evidence_by_event.get(hit.event_id),
                                                    weave_sources=weave_sources))
            for hit in active_hits
        }
        consumer_hits = {}
        for hit in active_hits:
            evidence = evidence_by_event.get(hit.event_id)
            formula_hit = formula_hits[hit.event_id]
            consumer_hits[hit.event_id] = (
                formula_hit if formula_hit.character_id == hit.character_id
                or evidence is not None and evidence.formula_context_kind.startswith("linko_coattack:") else hit
            )
        return cls(interval_index, group_intervals, tuple(active_hits), without_index, group_index,
                   BattleHitBuffProjectionCache(without_index, memo=memo),
                   BattleHitBuffProjectionCache(group_index, memo=memo), formula_hits, consumer_hits)

    def require_inputs(self, interval_index: BattleBuffIntervalIndex,
                       group_intervals: tuple[BattleInferredBuffInterval, ...],
                       active_hits: Sequence[BattleAnalysisHit], memo: BattleBuffProjectionMemo) -> None:
        if (self.source_index is not interval_index or self.group_intervals != group_intervals
                or self.active_hits != tuple(active_hits)
                or self.candidate_cache.memo is not memo or self.group_cache.memo is not memo):
            raise ValueError("Prepared Buff candidate belongs to another frozen plan or projection request")
        self.candidate_cache.require_intervals(self.without_index)
        self.group_cache.require_intervals(self.group_index)


def prepare_buff_candidate_projection_batch(
    plans: Sequence[BattleBuffCounterfactualGroupPlan], *, outgoing_hits: Sequence[BattleAnalysisHit],
    interval_index: BattleBuffIntervalIndex, memo: BattleBuffProjectionMemo,
    evidence_by_event: Mapping[str, BattleSkillDamageEvidence],
    progress_callback: BattleAnalysisProgressCallback | None = None,
) -> dict[str, PreparedBuffCandidateProjection]:
    """Join native projection work without changing the independently evaluated candidate plans."""
    if memo.native is None:
        return {}
    formula_by_event = {}
    replay_formula_hits = []
    sources = BattleWeaveSourceIndex(outgoing_hits)
    for ordinal, hit in enumerate(outgoing_hits):
        if ordinal % 64 == 0:
            report_battle_analysis_progress(progress_callback, phase="buff_counterfactual_prepare",
                message="正在整理全部 Buff 候选的冻结公式命中…")
        formula_hit = project_formula_hit(hit, evidence_by_event.get(hit.event_id), weave_sources=sources)
        formula_by_event[hit.event_id] = formula_hit
        replay_formula_hits.append(formula_hit)
    prepared = {}
    groups = []
    for ordinal, plan in enumerate(plans):
        report_battle_analysis_progress(progress_callback, phase="buff_counterfactual_prepare",
            message="正在联合准备所有 Buff 移除候选的逐击投影…", completed=ordinal, total=len(plans))
        if not plan.active_hits:
            continue
        pair = PreparedBuffCandidateProjection.create(interval_index=interval_index,
            group_intervals=plan.intervals, active_hits=plan.active_hits, memo=memo,
            evidence_by_event=evidence_by_event, formula_by_event=formula_by_event)
        prepared[plan.group_key] = pair
        # Full formula axis is needed by stateful replay; raw hits are only consumed
        # for the plan's active beneficiaries. Both retain their original evidence.
        groups.append((pair.candidate_cache, (*replay_formula_hits, *plan.active_hits, *pair.formula_hits.values())))
        groups.append((pair.group_cache, pair.consumer_hits.values()))
    BattleHitBuffProjectionCache.prepare_many(groups)
    return prepared
