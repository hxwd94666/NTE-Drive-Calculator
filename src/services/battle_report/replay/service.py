# 按明确暴击候选与证据缺口确定性重放每一击。
from __future__ import annotations
from collections.abc import Mapping, Sequence
from dataclasses import replace
from src.domain.battle_report.analysis.snapshot import BattleAnalysisSnapshot
from src.domain.battle_report.buffs.models import BattleHitBuffProjection
from src.domain.battle_report.replay.models import BattleHitReplayResult, BattleSkillDamageEvidence
from src.services.battle_report.buffs.attribute_projection import BattleBuffAttributeProjectionService
from src.services.battle_report.buffs.interval_index import (
    BattleBuffIntervalIndex, BattleBuffIntervalQuery,
)
from src.services.battle_report.damage.composition import classify_battle_hit_channel
from src.services.battle_report.replay.special import BattleSpecialHitReplayService
from src.services.battle_report.replay.topple import (
    BattleToppleCharacterConfig, BattleToppleHitReplayService,
)
from src.services.battle_report.replay.support import (
    apply_observed_damage_correction, reanchor_direct_replay_result,
)
from src.services.battle_report.replay.audit import BattleHitReplayAuditService
from src.services.battle_report.replay.formula_catalog import (
    DIRECT_FORMULA_CHANNELS as _DIRECT_FORMULA_CHANNELS,
)
from src.services.battle_report.replay.selected_context import (
    PreparedReplayAuditContext, PreparedReplayAuditInputs,
)
from src.services.battle_report.targets.instance_mapping import BattleTargetInstanceMappingService
from src.services.battle_report.analysis.progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_report.replay.formula_cache import full_replay_formula_cache_key
from src.services.battle_report.replay.buff_cache import (
    BattleHitBuffProjectionCache,
)
from src.services.battle_report.damage.weave_source import BattleWeaveSourceIndex, find_paired_weave_source_hit
from src.services.battle_report.replay.formula_projection import project_formula_hit, project_replay_formula_context
from src.services.battle_report.replay.direct_rendering import BattleDirectHitReplayMixin
from src.domain.battle_report.analysis.backend_contracts import DirectFormulaBackend, available_battle_compute
from src.services.battle_report.replay.direct_batch import DirectReplayBatch
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.replay.special_batch import SpecialReplayBatch, PendingSpecialReplay

HIT_REPLAY_MODEL_VERSION = "battle-hit-replay-v38"
class BattleHitReplayService(BattleDirectHitReplayMixin):
    @classmethod
    def replay(
        cls,
        analysis: BattleAnalysisSnapshot,
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        *,
        topple_character_configs: (
            Mapping[int, BattleToppleCharacterConfig] | None
        ) = None,
        apply_observed_refinements: bool = True,
        prepared_audit_context: PreparedReplayAuditContext | None = None,
        prepared_audit_inputs: PreparedReplayAuditInputs | None = None,
        buff_interval_index: BattleBuffIntervalQuery | None = None,
        buff_projection_cache: BattleHitBuffProjectionCache | None = None,
        projection_by_event: Mapping[str, BattleHitBuffProjection] | None = None,
        progress_callback: BattleAnalysisProgressCallback | None = None,
        direct_formula_backend: DirectFormulaBackend | None = None,
        progress_phase: str = "hit_replay",
        progress_message: str = "正在执行固定轴逐击公式重放…",
    ) -> tuple[BattleHitReplayResult, ...]:
        if prepared_audit_context is not None:
            prepared_audit_context.require_selected_replay()
        evidence_by_event = (
            prepared_audit_inputs.evidence_by_event
            if prepared_audit_inputs is not None
            else {row.event_id: row for row in skill_evidence}
        )
        baselines = (
            prepared_audit_inputs.baselines_by_character
            if prepared_audit_inputs is not None
            else {row.character_id: row for row in analysis.baselines}
        )
        baseline_values = (
            prepared_audit_inputs.baseline_values_by_character
            if prepared_audit_inputs is not None
            else {}
        )
        buff_intervals = (
            buff_interval_index
            if buff_interval_index is not None
            else BattleBuffIntervalIndex(
                getattr(analysis, "buff_intervals", ())
            )
        )
        projection_cache = (
            BattleHitBuffProjectionCache(buff_intervals, memo=BattleBuffProjectionMemo(direct_formula_backend, progress_callback))
            if buff_projection_cache is None else buff_projection_cache
        )
        projection_cache.require_intervals(buff_intervals)
        replay_hits = tuple(
            hit
            for hit in analysis.hits
            if hit.direction == "outgoing"
            and (
                prepared_audit_context is None
                or hit.event_id in prepared_audit_context.selected_event_ids
            )
        )
        total_hits = len(replay_hits)
        report_battle_analysis_progress(
            progress_callback,
            phase=progress_phase,
            message=progress_message,
            completed=0,
            total=total_hits,
        )
        weave_source_index = BattleWeaveSourceIndex(analysis.hits)
        formula_hits = []
        for hit in replay_hits:
            formula_hit = project_formula_hit(hit, evidence_by_event.get(hit.event_id))
            source_hit = None
            if classify_battle_hit_channel(hit)[0] == "reaction_hexed":
                source_hit = find_paired_weave_source_hit(hit, weave_source_index)
                if source_hit is not None:
                    formula_hit = replace(formula_hit, character_id=source_hit.character_id)
            formula_hits.append((formula_hit, source_hit))
        projection_hits = [
            formula_hit for hit, (formula_hit, _) in zip(replay_hits, formula_hits, strict=True)
            if projection_by_event is None or projection_by_event.get(hit.event_id) is None
        ]
        for hit in replay_hits:
            channel_id = classify_battle_hit_channel(hit)[0]
            if channel_id in {"other_topple", "special_daffodill_extra_topple"}:
                projection_hits.extend(BattleToppleHitReplayService.projection_hits(
                    hit, analysis, topple_character_configs or {},
                    source_character_id=1054 if channel_id == "special_daffodill_extra_topple" else None,
                ))
        projection_cache.prepare(projection_hits)
        target_analysis_by_key: dict[tuple[str, str], BattleAnalysisSnapshot] = {}
        direct_formula_cache: dict[object, BattleHitReplayResult] = {}
        results = []
        direct_batch = DirectReplayBatch(direct_formula_backend, cls._replay_direct)
        special_batch = SpecialReplayBatch(direct_formula_backend)
        pending_special = []

        def append_special(result, hit, formula_hit, evidence):
            if isinstance(result, PendingSpecialReplay):
                pending_special.append((len(results), result.index, hit, formula_hit, evidence))
                results.append(None)
            else:
                results.append(apply_observed_damage_correction(
                    project_replay_formula_context(result, formula_hit, evidence), hit,
                ))
        for ordinal, (hit, (formula_hit, source_hit)) in enumerate(zip(replay_hits, formula_hits, strict=True), start=1):
            if ordinal > 1 and (ordinal - 1) % 64 == 0:
                report_battle_analysis_progress(
                    progress_callback,
                    phase=progress_phase,
                    message=progress_message,
                    completed=ordinal - 1,
                    total=total_hits,
                )
            channel_id, formula_label = classify_battle_hit_channel(hit)
            evidence = evidence_by_event.get(hit.event_id)
            baseline = baselines.get(formula_hit.character_id)
            if (
                prepared_audit_inputs is not None
                and hit.event_id
                in prepared_audit_inputs.target_condition_by_event
            ):
                hit_analysis = replace(
                    analysis,
                    target_condition=(
                        prepared_audit_inputs.target_condition_by_event[
                            hit.event_id
                        ]
                    ),
                )
            else:
                target_key = (
                    str(getattr(hit, "scope_half", "") or "").casefold(),
                    str(getattr(hit, "target_id", "") or ""),
                )
                hit_analysis = target_analysis_by_key.get(target_key)
                if hit_analysis is None:
                    hit_analysis = BattleTargetInstanceMappingService.analysis_for_hit(
                        analysis,
                        hit,
                    )
                    target_analysis_by_key[target_key] = hit_analysis
            if channel_id == "special_daffodill_extra_topple":
                result = special_batch.submit(BattleToppleHitReplayService.replay,
                    hit=hit, analysis=hit_analysis,
                    character_configs=topple_character_configs or {},
                    source_character_id=1054, formula_type="达芙蒂尔·额外倾陷伤害",
                    projection_for_hit=projection_cache.project,
                )
                append_special(result, hit, formula_hit, evidence)
                continue
            if channel_id == "other_topple":
                result = special_batch.submit(BattleToppleHitReplayService.replay,
                    hit=hit,
                    analysis=hit_analysis,
                    character_configs=topple_character_configs or {},
                    projection_for_hit=projection_cache.project,
                )
                append_special(result, hit, formula_hit, evidence)
                continue
            if channel_id == "special_fadia_shared_damage":
                result = cls._unreplayable(
                    hit.event_id,
                    hit.damage,
                    (
                        "破灭体验按法帝娅实际承受伤害转移（基础 300%，二觉 600%）；"
                        "当前封包只有护盾与分摊前的受击值，尚不能安全还原实际承受值"
                    ),
                    formula_label,
                )
                results.append(apply_observed_damage_correction(project_replay_formula_context(result, formula_hit, evidence), hit))
                continue
            if baseline is None and (channel_id != "reaction_hexed" or source_hit is not None):
                result = cls._unreplayable(
                    hit.event_id,
                    hit.damage,
                    "缺少角色面板",
                    formula_label,
                )
                results.append(apply_observed_damage_correction(project_replay_formula_context(result, formula_hit, evidence), hit))
                continue
            if channel_id == "reaction_hexed":
                projection = (
                    None
                    if projection_by_event is None
                    else projection_by_event.get(hit.event_id)
                )
                if projection is None:
                    projection = projection_cache.project(formula_hit)
                frozen = baseline_values.get(formula_hit.character_id) or {
                    row.property_id: row.value
                    for row in (() if baseline is None else baseline.stats)
                }
                values = BattleBuffAttributeProjectionService.apply_additive(
                    frozen,
                    projection,
                )
                result = special_batch.submit(BattleSpecialHitReplayService.replay,
                    channel_id=channel_id,
                    formula_label=formula_label,
                    hit=formula_hit,
                    evidence=evidence,
                    projection=projection,
                    values=values,
                    analysis=hit_analysis,
                )
                assert result is not None
                append_special(result, hit, formula_hit, evidence)
                continue
            if evidence is None:
                result = cls._unreplayable(
                    hit.event_id,
                    hit.damage,
                    "缺少等级解析后的技能倍率",
                    formula_label,
                )
                results.append(apply_observed_damage_correction(project_replay_formula_context(result, formula_hit, evidence), hit))
                continue
            if getattr(hit_analysis, "target_condition", None) is None:
                result = cls._unreplayable(
                    hit.event_id,
                    hit.damage,
                    "尚未保存用户确认的单目标防御与抗性",
                    "直伤",
                )
                results.append(apply_observed_damage_correction(project_replay_formula_context(result, formula_hit, evidence), hit))
                continue
            projection = (
                None
                if projection_by_event is None
                else projection_by_event.get(hit.event_id)
            )
            if projection is None:
                projection = projection_cache.project(formula_hit)
            frozen = baseline_values.get(formula_hit.character_id) or {
                row.property_id: row.value for row in baseline.stats
            }
            values = BattleBuffAttributeProjectionService.apply_additive(
                frozen,
                projection,
            )
            if channel_id not in _DIRECT_FORMULA_CHANNELS:
                special = special_batch.submit(BattleSpecialHitReplayService.replay,
                    channel_id=channel_id,
                    formula_label=formula_label,
                    hit=formula_hit,
                    evidence=evidence,
                    projection=projection,
                    values=values,
                    analysis=hit_analysis,
                )
                result = (
                    special
                    if special is not None
                    else cls._unreplayable(
                        hit.event_id,
                        hit.damage,
                        f"{formula_label} 需要独立逐击重放适配器",
                        formula_label,
                    )
                )
                append_special(result, hit, formula_hit, evidence)
                continue
            rendered_formula_label = (
                formula_label
                if channel_id in {
                    "direct",
                    "direct_follow_up",
                    "special_lacrimosa_dissonance",
                }
                else f"直伤（{formula_label}）"
            )
            cache_key = full_replay_formula_cache_key(
                channel_id=channel_id,
                formula_label=rendered_formula_label,
                hit=formula_hit,
                evidence=evidence,
                baseline=baseline,
                projection=projection,
                values=values,
                analysis=hit_analysis,
            )
            direct_kwargs = dict(
                hit=formula_hit,
                evidence=evidence,
                baseline=baseline,
                projection=projection,
                values=values,
                character_level=baseline.character_level,
                analysis=hit_analysis,
                applied_intervals=projection.applied_interval_ids,
                excluded_intervals=projection.excluded_interval_ids,
                formula_label=rendered_formula_label,
            )
            if direct_formula_backend is not None:
                direct_batch.add(
                    results, cache_key, direct_kwargs, hit, formula_hit, evidence,
                )
                continue
            template = (
                None if cache_key is None else direct_formula_cache.get(cache_key)
            )
            if template is None:
                result = cls._replay_direct(**direct_kwargs)
                if cache_key is not None:
                    direct_formula_cache[cache_key] = result
            else:
                result = reanchor_direct_replay_result(template, formula_hit)
            results.append(apply_observed_damage_correction(
                project_replay_formula_context(result, formula_hit, evidence), hit,
            ))
        direct_batch.finish(results, checkpoint=lambda: report_battle_analysis_progress(
            progress_callback, phase=progress_phase, message=progress_message,
            completed=sum(row is not None for row in results), total=total_hits,
        ))
        special_results = special_batch.resolve(checkpoint=lambda: report_battle_analysis_progress(
            progress_callback, phase=progress_phase, message=progress_message,
            completed=sum(row is not None for row in results), total=total_hits,
        ))
        for ordinal, (position, index, hit, formula_hit, evidence) in enumerate(pending_special):
            if ordinal % 64 == 0:
                report_battle_analysis_progress(
                    progress_callback, phase="replay", message="正在恢复特殊伤害逐击证据…",
                )
            results[position] = apply_observed_damage_correction(
                project_replay_formula_context(special_results[index], formula_hit, evidence), hit,
            )
        raw_results = tuple(results)
        if prepared_audit_context is not None:
            report_battle_analysis_progress(
                progress_callback, phase=progress_phase,
                message=progress_message, completed=total_hits, total=total_hits,
            )
            return prepared_audit_context.freeze_candidate_branches(raw_results)
        if not apply_observed_refinements:
            report_battle_analysis_progress(
                progress_callback, phase=progress_phase,
                message=progress_message, completed=total_hits, total=total_hits,
            )
            return raw_results
        return BattleHitReplayAuditService.postprocess(
            analysis, raw_results, progress_callback=progress_callback,
            compute_backend=available_battle_compute(direct_formula_backend),
        )
