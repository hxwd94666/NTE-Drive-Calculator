# 在固定真实逐击轴上逐个移除 Buff，计算选定时段的独立伤害收益。
"""Per-Buff removal counterfactuals calibrated to observed battle damage."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleBuffCounterfactualResult,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
    BattleSkillDamageEvidence,
)
from src.services.battle_buff_inference_service import BattleBuffInferenceService
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.battle_topple_hit_replay_service import BattleToppleCharacterConfig


BUFF_COUNTERFACTUAL_MODEL_VERSION = "battle-buff-counterfactual-v1"
_CONFIDENCE_ORDER = {"未解析": 0, "低": 1, "中": 2, "高": 3}


def battle_buff_counterfactual_key(interval: BattleInferredBuffInterval) -> str:
    """Return the stable source identity used by the Service and presentation."""

    source_identity = (
        getattr(interval, "source_effect_definition_id", "")
        or getattr(interval, "buff_asset_path", "")
        or getattr(interval, "buff_name", "")
    )
    return "\x1f".join((
        str(getattr(interval, "source_character_id", 0)),
        source_identity,
        getattr(interval, "buff_asset_path", ""),
        getattr(interval, "target_scope", "unknown"),
    ))


def _replay_value(replay: BattleHitReplayResult | None) -> float | None:
    if replay is None:
        return None
    if replay.expected_damage is not None and replay.expected_damage > 0:
        return float(replay.expected_damage)
    if replay.selected_damage is not None and replay.selected_damage > 0:
        return float(replay.selected_damage)
    return None


def _safe_ratio(candidate: float, baseline: float) -> float | None:
    if baseline <= 0 or candidate < 0:
        return None
    ratio = candidate / baseline
    if ratio != ratio or ratio == float("inf"):
        return None
    return max(0.0, min(100.0, ratio))


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
        baseline_by_event = {row.event_id: row for row in baseline_replays}
        groups: dict[str, list[BattleInferredBuffInterval]] = defaultdict(list)
        for interval in analysis.buff_intervals:
            groups[battle_buff_counterfactual_key(interval)].append(interval)
        return tuple(
            cls._calculate_group(
                analysis=analysis,
                outgoing_hits=outgoing_hits,
                baseline_by_event=baseline_by_event,
                group_key=group_key,
                group_intervals=tuple(group_intervals),
                skill_evidence=skill_evidence,
                topple_character_configs=topple_character_configs,
            )
            for group_key, group_intervals in sorted(
                groups.items(),
                key=lambda item: (
                    item[1][0].source_character_name,
                    item[1][0].buff_name,
                    item[0],
                ),
            )
        )

    @classmethod
    def _calculate_group(
        cls,
        *,
        analysis: BattleAnalysisSnapshot,
        outgoing_hits: Sequence[BattleAnalysisHit],
        baseline_by_event: Mapping[str, BattleHitReplayResult],
        group_key: str,
        group_intervals: tuple[BattleInferredBuffInterval, ...],
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        topple_character_configs: (
            Mapping[int, BattleToppleCharacterConfig] | None
        ),
    ) -> BattleBuffCounterfactualResult:
        first = group_intervals[0]
        active_hits = tuple(
            hit
            for hit in outgoing_hits
            if BattleBuffInferenceService.active_for_hit(group_intervals, hit)
        )
        active_ids = {hit.event_id for hit in active_hits}
        projected_ids = {
            hit.event_id
            for hit in active_hits
            if any(
                decision.status == "applied"
                for decision in BattleBuffAttributeProjectionService.project_hit(
                    hit,
                    group_intervals,
                ).decisions
            )
        }
        without_by_event: dict[str, BattleHitReplayResult] = {}
        if active_hits and any(row.modifiers for row in group_intervals):
            removed_ids = {row.interval_id for row in group_intervals}
            without_analysis = replace(
                analysis,
                hits=active_hits,
                buff_intervals=tuple(
                    row
                    for row in analysis.buff_intervals
                    if row.interval_id not in removed_ids
                ),
                hit_replays=(),
                buff_counterfactuals=(),
            )
            without_replays = BattleHitReplayService.replay(
                without_analysis,
                skill_evidence,
                topple_character_configs=topple_character_configs,
            )
            without_by_event = {row.event_id: row for row in without_replays}

        predicted_hits: dict[str, float] = {}
        quantified_ids: set[str] = set()
        for hit in outgoing_hits:
            predicted = float(hit.damage)
            if hit.event_id in active_ids:
                baseline_value = _replay_value(baseline_by_event.get(hit.event_id))
                without_value = _replay_value(without_by_event.get(hit.event_id))
                ratio = (
                    None
                    if baseline_value is None or without_value is None
                    else _safe_ratio(without_value, baseline_value)
                )
                if ratio is not None:
                    predicted *= ratio
                    if hit.event_id in projected_ids or abs(ratio - 1.0) > 1e-12:
                        quantified_ids.add(hit.event_id)
            predicted_hits[hit.event_id] = predicted

        baseline_hit_damage = sum(float(hit.damage) for hit in outgoing_hits)
        without_hit_damage = sum(predicted_hits.values())
        baseline_vital_damage = sum(
            max(0.0, float(event.effective_hp_loss))
            for event in analysis.max_hp_events
        )
        without_vital_damage, quantified_vital_damage = cls._without_vital_damage(
            analysis,
            predicted_hits,
            {hit.event_id: hit for hit in outgoing_hits},
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
        damage_gain = derived_baseline - without_damage
        gain_percent = (
            damage_gain / without_damage * 100.0 if without_damage > 0 else 0.0
        )
        quantified_damage = sum(
            float(hit.damage)
            for hit in outgoing_hits
            if hit.event_id in quantified_ids
        ) + quantified_vital_damage
        quantified_percent = (
            quantified_damage / derived_baseline * 100.0
            if derived_baseline > 0
            else 0.0
        )
        if not active_hits:
            method = "not_covered"
            confidence = "未解析"
            explanation = "该 Buff 在当前选定时段没有覆盖对敌逐击，收益按 0 计。"
        elif not quantified_ids and quantified_vital_damage <= 0:
            method = "unquantified_zero_estimate"
            confidence = "低"
            explanation = (
                "当前已结构化公式不能量化该 Buff；原轴仍完整保留，"
                "本次移除增量暂估为 0。"
            )
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
            without_buff_damage=without_damage,
            damage_gain=damage_gain,
            gain_percent=gain_percent,
            quantified_damage=quantified_damage,
            quantified_percent=quantified_percent,
            confidence=confidence,
            method=method,
            explanation=explanation,
        )

    @staticmethod
    def _without_vital_damage(
        analysis: BattleAnalysisSnapshot,
        predicted_hits: Mapping[str, float],
        hits_by_event: Mapping[str, BattleAnalysisHit],
    ) -> tuple[float, float]:
        total = 0.0
        quantified = 0.0
        for event in analysis.max_hp_events:
            baseline = max(0.0, float(event.effective_hp_loss))
            predicted = baseline
            if event.mechanic_kind == "lacrimosa_nightmare_awaken_5":
                linked_ids = tuple(
                    event_id
                    for event_id in event.evidence_event_ids
                    if event_id in hits_by_event
                )
                linked_baseline = sum(
                    float(hits_by_event[event_id].damage)
                    for event_id in linked_ids
                )
                linked_without = sum(
                    predicted_hits.get(
                        event_id,
                        float(hits_by_event[event_id].damage),
                    )
                    for event_id in linked_ids
                )
                ratio = _safe_ratio(linked_without, linked_baseline)
                if ratio is not None and abs(ratio - 1.0) > 1e-12:
                    predicted *= ratio
                    quantified += baseline
            total += predicted
        return total, quantified
