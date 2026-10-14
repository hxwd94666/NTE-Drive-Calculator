# 为整场配装比较准备冻结逐击输入，统一批量执行正式公式与缺口比较。
from __future__ import annotations

from src.domain.battle_report.analysis.backend_contracts import available_battle_compute
from src.services.battle_report.analysis.progress import report_battle_analysis_progress
from src.services.battle_report.buffs.interval_index import BattleBuffIntervalIndex
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.characters.daffodill_marginal import BattleDaffodillMarginalService
from src.services.battle_report.replay.buff_cache import BattleHitBuffProjectionCache
from src.services.battle_report.native.counterfactual import compare_counterfactual_batch
from src.services.battle_report.targets.instance_mapping import BattleTargetInstanceMappingService


def prepare_build_comparison_ratios(
    original, candidate, structured_ratios, *, projection_memo=None, progress_callback=None,
):
    memo = projection_memo if projection_memo is not None else BattleBuffProjectionMemo()
    backend = available_battle_compute(None if memo.native is None else memo.native.backend)
    original_hits = {row.event_id: row for row in original.hits if row.direction == "outgoing"}
    candidate_hits = {row.event_id: row for row in candidate.hits if row.direction == "outgoing"}
    original_replays = {row.event_id: row for row in original.hit_replays}
    candidate_replays = {row.event_id: row for row in candidate.hit_replays}
    original_baselines = {row.character_id: row for row in original.baselines}
    candidate_baselines = {row.character_id: row for row in candidate.baselines}
    fallback_hits = tuple(hit for key, hit in original_hits.items() if structured_ratios[key] is None)
    left = BattleHitBuffProjectionCache(
        BattleBuffIntervalIndex(BattleDaffodillMarginalService.direct_formula_intervals(original)), memo=memo,
    )
    right = BattleHitBuffProjectionCache(
        BattleBuffIntervalIndex(BattleDaffodillMarginalService.direct_formula_intervals(candidate)), memo=memo,
    )
    BattleHitBuffProjectionCache.prepare_many((
        (left, fallback_hits), (right, (candidate_hits.get(hit.event_id, hit) for hit in fallback_hits)),
    ))
    result = dict(structured_ratios)
    jobs, events = [], []
    routed_by_target = {}

    def checkpoint():
        report_battle_analysis_progress(progress_callback, phase="build_compare",
                                       message="正在批量比较候选配置与当前基线…")

    for ordinal, (event_id, hit) in enumerate(original_hits.items()):
        if ordinal % 64 == 0:
            checkpoint()
        if structured_ratios[event_id] is not None and backend is None:
            continue
        job = {
            "hit": hit, "original_replay": original_replays.get(event_id),
            "candidate_replay": candidate_replays.get(event_id),
            "original_baseline": original_baselines.get(hit.character_id),
            "candidate_baseline": candidate_baselines.get(hit.character_id),
        }
        if structured_ratios[event_id] is None:
            key = hit.scope_half.casefold(), hit.target_id
            if key not in routed_by_target:
                routed_by_target[key] = BattleTargetInstanceMappingService.analysis_for_hit(original, hit)
            job.update(
                original_projection=left.project(hit),
                candidate_projection=right.project(candidate_hits.get(event_id, hit)),
                target_condition=routed_by_target[key].target_condition,
            )
        jobs.append(job)
        events.append(event_id)
    ratios = compare_counterfactual_batch(jobs, backend=backend, checkpoint=checkpoint)
    result.update(zip(events, ratios, strict=True))
    return result
