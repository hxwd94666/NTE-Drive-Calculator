# 在固定真实逐击轴上逐个移除 Buff，计算选定时段的独立伤害收益。
"""Per-Buff removal counterfactuals calibrated to observed battle damage."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from src.domain.battle_buff_counterfactual import (
    BattleBuffBeneficiaryResult,
    BattleBuffCounterfactualResult,
)
from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleDamageQuantification,
    BattleQuantificationGap,
    QuantificationStatus,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
    BattleSkillDamageEvidence,
)
from src.services.battle_buff_inference_service import BattleBuffInferenceService
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.battle_hit_counterfactual_ratio_service import (
    BattleHitCounterfactualRatioService,
)
from src.services.battle_target_instance_mapping_service import (
    BattleTargetInstanceMappingService,
)
from src.services.battle_topple_hit_replay_service import BattleToppleCharacterConfig


BUFF_COUNTERFACTUAL_MODEL_VERSION = "battle-buff-counterfactual-v4"
_CONFIDENCE_ORDER = {"未解析": 0, "低": 1, "中": 2, "高": 3}


@dataclass(frozen=True, slots=True)
class _HitProjection:
    hit: BattleAnalysisHit
    predicted_damage: float
    quantification: BattleCounterfactualRatio


@dataclass(frozen=True, slots=True)
class _VitalProjection:
    event_id: str
    character_id: int | None
    baseline_damage: float
    predicted_damage: float
    status: QuantificationStatus
    gaps: tuple[BattleQuantificationGap, ...] = ()


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
            if interval.source_kind == "candidate_derived_awakening_settlement":
                continue
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
        without_by_event: dict[str, BattleHitReplayResult] = {}
        without_intervals = tuple(
            row
            for row in analysis.buff_intervals
            if row.interval_id not in {item.interval_id for item in group_intervals}
        )
        if active_hits and any(row.modifiers for row in group_intervals):
            without_analysis = replace(
                analysis,
                hits=tuple(outgoing_hits),
                buff_intervals=without_intervals,
                hit_replays=(),
                buff_counterfactuals=(),
            )
            without_replays = BattleHitReplayService.replay(
                without_analysis,
                skill_evidence,
                topple_character_configs=topple_character_configs,
            )
            without_by_event = {row.event_id: row for row in without_replays}

        baselines = {row.character_id: row for row in analysis.baselines}
        evidence_by_event = {row.event_id: row for row in skill_evidence}
        hit_projections: dict[str, _HitProjection] = {}
        for hit in outgoing_hits:
            if hit.event_id not in active_ids:
                ratio = BattleCounterfactualRatio.not_applicable(
                    method="buff_not_active",
                    explanation="该 Buff 在本击时刻未生效，原击确定保持不变。",
                )
            else:
                group_projection = BattleBuffAttributeProjectionService.project_hit(
                    hit,
                    group_intervals,
                )
                original_projection = BattleBuffAttributeProjectionService.project_hit(
                    hit,
                    analysis.buff_intervals,
                )
                candidate_projection = BattleBuffAttributeProjectionService.project_hit(
                    hit,
                    without_intervals,
                )
                evidence = evidence_by_event.get(hit.event_id)
                formula_character_id = (
                    evidence.source_character_id
                    if evidence is not None
                    and evidence.source_character_id is not None
                    else hit.character_id
                )
                hit_analysis = BattleTargetInstanceMappingService.analysis_for_hit(
                    analysis,
                    hit,
                )
                ratio = BattleHitCounterfactualRatioService.compare(
                    hit=hit,
                    original_baseline=baselines.get(formula_character_id),
                    candidate_baseline=baselines.get(formula_character_id),
                    original_projection=original_projection,
                    candidate_projection=candidate_projection,
                    skill_evidence=evidence,
                    original_replay=baseline_by_event.get(hit.event_id),
                    candidate_replay=without_by_event.get(hit.event_id),
                    target_condition=hit_analysis.target_condition,
                )
                ratio = cls._resolve_inactive_projection(
                    ratio,
                    group_projection=group_projection,
                    group_intervals=group_intervals,
                )
            quantified_ratio = ratio.quantified_ratio
            predicted = float(hit.damage) * (
                quantified_ratio if quantified_ratio is not None else 1.0
            )
            hit_projections[hit.event_id] = _HitProjection(
                hit=hit,
                predicted_damage=predicted,
                quantification=ratio,
            )

        predicted_hits = {
            event_id: row.predicted_damage
            for event_id, row in hit_projections.items()
        }
        quantified_ids = {
            event_id
            for event_id, row in hit_projections.items()
            if row.quantification.status in {"complete", "partial"}
        }

        baseline_hit_damage = sum(float(hit.damage) for hit in outgoing_hits)
        without_hit_damage = sum(predicted_hits.values())
        baseline_vital_damage = sum(
            max(0.0, float(event.effective_hp_loss))
            for event in analysis.max_hp_events
        )
        vital_projections = cls._vital_projections(
            analysis,
            hit_projections,
        )
        without_vital_damage = sum(
            row.predicted_damage for row in vital_projections
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
        quantification = cls._aggregate_quantification(
            hit_projections=tuple(hit_projections.values()),
            vital_projections=vital_projections,
            fixed_derived_damage=fixed_derived_damage,
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
            for row in vital_projections
            if row.character_id is not None
            and int(row.character_id) > 0
            and row.status in {"complete", "partial", "unavailable"}
        }
        character_names = {
            int(hit.character_id): hit.character_name
            for hit in outgoing_hits
            if hit.character_id is not None and int(hit.character_id) > 0
        }
        character_names.update({
            int(event.source_character_id): event.source_character_name
            for event in analysis.max_hp_events
            if event.source_character_id is not None
            and int(event.source_character_id) > 0
        })
        beneficiaries = tuple(
            cls._beneficiary_result(
                character_id=character_id,
                character_name=character_names.get(
                    character_id,
                    f"角色 {character_id}",
                ),
                hit_projections=tuple(hit_projections.values()),
                active_ids=active_ids,
                vital_projections=tuple(
                    row
                    for row in vital_projections
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
        )

    @staticmethod
    def _beneficiary_result(
        *,
        character_id: int,
        character_name: str,
        hit_projections: Sequence[_HitProjection],
        active_ids: set[str],
        vital_projections: Sequence[_VitalProjection],
        team_without_quantified_effect_damage: float | None,
        team_without_buff_damage: float | None,
    ) -> BattleBuffBeneficiaryResult:
        role_hits = tuple(
            row for row in hit_projections if row.hit.character_id == character_id
        )
        baseline_damage = (
            sum(float(row.hit.damage) for row in role_hits)
            + sum(row.baseline_damage for row in vital_projections)
        )
        without_damage = (
            sum(row.predicted_damage for row in role_hits)
            + sum(row.predicted_damage for row in vital_projections)
        )
        known_gain = baseline_damage - without_damage
        quantification = BattleBuffCounterfactualService._aggregate_quantification(
            hit_projections=role_hits,
            vital_projections=vital_projections,
            fixed_derived_damage=0.0,
            quantified_increment=known_gain,
        )
        quantified_gain = (
            None if quantification.status == "unavailable" else known_gain
        )
        full_available = quantification.status in {"complete", "not_applicable"}
        full_gain = known_gain if full_available else None
        return BattleBuffBeneficiaryResult(
            character_id=character_id,
            character_name=character_name,
            affected_hits=sum(row.hit.event_id in active_ids for row in role_hits),
            quantified_hits=sum(
                row.quantification.status in {"complete", "partial"}
                for row in role_hits
            ),
            baseline_damage=baseline_damage,
            without_quantified_effect_damage=(
                without_damage
                if quantification.status != "unavailable"
                else None
            ),
            quantified_damage_gain=quantified_gain,
            quantified_recipient_gain_percent=(
                quantified_gain / without_damage * 100.0
                if quantified_gain is not None and without_damage > 0
                else (0.0 if quantified_gain is not None else None)
            ),
            quantified_team_contribution_percent=(
                quantified_gain / team_without_quantified_effect_damage * 100.0
                if quantified_gain is not None
                and team_without_quantified_effect_damage is not None
                and team_without_quantified_effect_damage > 0
                else (0.0 if quantified_gain is not None else None)
            ),
            without_buff_damage=without_damage if full_available else None,
            damage_gain=full_gain,
            recipient_gain_percent=(
                full_gain / without_damage * 100.0
                if full_gain is not None and without_damage > 0
                else (0.0 if full_gain is not None else None)
            ),
            team_contribution_percent=(
                full_gain / team_without_buff_damage * 100.0
                if full_gain is not None
                and team_without_buff_damage is not None
                and team_without_buff_damage > 0
                else (0.0 if full_gain is not None else None)
            ),
            quantification=quantification,
        )

    @classmethod
    def _resolve_inactive_projection(
        cls,
        ratio: BattleCounterfactualRatio,
        *,
        group_projection: BattleHitBuffProjection,
        group_intervals: Sequence[BattleInferredBuffInterval],
    ) -> BattleCounterfactualRatio:
        if ratio.status != "not_applicable":
            return ratio
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
        lacks_formula = not any(row.modifiers for row in group_intervals)
        if not applied and not unresolved and not lacks_formula:
            return ratio
        explanation = (
            "Buff 修正已投影，但当前逐击公式尚未映射该变化。"
            if applied
            else (
                "Buff 存在未解析的数值或 Calculation，不能证明本击不受影响。"
                if unresolved
                else "Buff 缺少可计算的属性修正，不能把收益记为 0。"
            )
        )
        gap = BattleQuantificationGap(
            code="formula_family_unsupported",
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

    @classmethod
    def _vital_projections(
        cls,
        analysis: BattleAnalysisSnapshot,
        hit_projections: Mapping[str, _HitProjection],
    ) -> tuple[_VitalProjection, ...]:
        result = []
        for event in analysis.max_hp_events:
            baseline = max(0.0, float(event.effective_hp_loss))
            predicted = baseline
            character_id = (
                int(event.source_character_id)
                if event.source_character_id is not None
                and int(event.source_character_id) > 0
                else None
            )
            status: QuantificationStatus = "not_applicable"
            gaps: tuple[BattleQuantificationGap, ...] = ()
            if event.mechanic_kind == "lacrimosa_nightmare_awaken_5":
                linked_ids = tuple(
                    event_id
                    for event_id in event.evidence_event_ids
                    if event_id in hit_projections
                )
                linked_baseline = sum(
                    float(hit_projections[event_id].hit.damage)
                    for event_id in linked_ids
                )
                linked_without = sum(
                    hit_projections[event_id].predicted_damage
                    for event_id in linked_ids
                )
                ratio = _safe_ratio(linked_without, linked_baseline)
                linked_rows = tuple(hit_projections[event_id] for event_id in linked_ids)
                gaps = tuple(dict.fromkeys(
                    gap
                    for row in linked_rows
                    for gap in row.quantification.gaps
                ))
                quantified = any(
                    row.quantification.status in {"complete", "partial"}
                    for row in linked_rows
                )
                unresolved = any(
                    row.quantification.status in {"partial", "unavailable"}
                    for row in linked_rows
                )
                if quantified and unresolved:
                    status = "partial"
                elif quantified:
                    status = "complete"
                elif unresolved:
                    status = "unavailable"
                if ratio is not None and status in {"complete", "partial"}:
                    predicted *= ratio
            result.append(_VitalProjection(
                event_id=event.event_id,
                character_id=character_id,
                baseline_damage=baseline,
                predicted_damage=predicted,
                status=status,
                gaps=gaps,
            ))
        return tuple(result)

    @staticmethod
    def _aggregate_quantification(
        *,
        hit_projections: Sequence[_HitProjection],
        vital_projections: Sequence[_VitalProjection],
        fixed_derived_damage: float,
        quantified_increment: float,
    ) -> BattleDamageQuantification:
        fully_quantified = sum(
            float(row.hit.damage)
            for row in hit_projections
            if row.quantification.status == "complete"
        ) + sum(
            row.baseline_damage
            for row in vital_projections
            if row.status == "complete"
        )
        partially_quantified = sum(
            float(row.hit.damage)
            for row in hit_projections
            if row.quantification.status == "partial"
        ) + sum(
            row.baseline_damage
            for row in vital_projections
            if row.status == "partial"
        )
        unavailable = sum(
            float(row.hit.damage)
            for row in hit_projections
            if row.quantification.status == "unavailable"
        ) + sum(
            row.baseline_damage
            for row in vital_projections
            if row.status == "unavailable"
        )
        proven_unchanged = fixed_derived_damage + sum(
            float(row.hit.damage)
            for row in hit_projections
            if row.quantification.status == "not_applicable"
        ) + sum(
            row.baseline_damage
            for row in vital_projections
            if row.status == "not_applicable"
        )
        gaps = tuple(dict.fromkeys((
            *(
                gap
                for row in hit_projections
                for gap in row.quantification.gaps
            ),
            *(
                gap
                for row in vital_projections
                for gap in row.gaps
            ),
        )))
        quantified_damage = fully_quantified + partially_quantified
        if unavailable > 0.0 and quantified_damage <= 0.0:
            status: QuantificationStatus = "unavailable"
        elif partially_quantified > 0.0 or unavailable > 0.0:
            status = "partial"
        elif fully_quantified > 0.0:
            status = "complete"
        else:
            status = "not_applicable"
        increment = (
            None
            if status == "unavailable"
            else (0.0 if status == "not_applicable" else quantified_increment)
        )
        return BattleDamageQuantification.from_buckets(
            status=status,
            fully_quantified_damage=fully_quantified,
            partially_quantified_damage=partially_quantified,
            unavailable_damage=unavailable,
            proven_unchanged_damage=proven_unchanged,
            quantified_increment=increment,
            gaps=gaps,
        )
