# 将静态推算 Buff 收窄投影为逐击反事实可用的加法属性。
"""Safe per-hit attribute projections from inferred Buff intervals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.buffs.models import (
    BattleBuffProjectionDecision, BattleHitBuffProjection, BattleInferredBuffInterval, BattleProjectedBuffModifier,
)
from src.services.battle_report.buffs.inference import BattleBuffInferenceService
from src.services.battle_report.buffs.interval_index import (
    BattleBuffIntervalIndex,
    BattleBuffIntervalQuery,
    BattleBuffIntervalIndexView,
)
from src.services.battle_report.damage.composition import classify_battle_hit_channel
from src.services.battle_report.forks.hit_adjustment import (
    BattleForkHitAdjustmentService,
)


from src.services.battle_report.buffs.projection_rules import (
    BUFF_ATTRIBUTE_PROJECTION_VERSION as BUFF_ATTRIBUTE_PROJECTION_VERSION,
    normalize_battle_buff_property_id as normalize_battle_buff_property_id,
    _minimum_confidence, _UNRESOLVED_REASON_MARKERS, evaluate_interval_projection,
)
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo


class BattleBuffAttributeProjectionService:
    """Project only non-duplicated, direct additive runtime Buff modifiers."""

    @classmethod
    def project_hit(
        cls,
        hit: BattleAnalysisHit,
        intervals: (
            Sequence[BattleInferredBuffInterval]
            | BattleBuffIntervalQuery
        ),
        *,
        active_intervals: Sequence[BattleInferredBuffInterval] | None = None,
        temporal_intervals: Sequence[BattleInferredBuffInterval] | None = None,
        projection_memo: BattleBuffProjectionMemo | None = None,
    ) -> BattleHitBuffProjection:
        indexed_intervals = isinstance(
            intervals,
            (BattleBuffIntervalIndex, BattleBuffIntervalIndexView),
        )
        temporal = (
            tuple(temporal_intervals)
            if temporal_intervals is not None
            else (
                intervals.temporal_for_hit(hit)
                if indexed_intervals
                else intervals
            )
        )
        active = (
            tuple(active_intervals)
            if active_intervals is not None
            else BattleBuffInferenceService.active_for_hit(intervals, hit)
        )
        channel_id = classify_battle_hit_channel(hit)[0]
        selected: dict[
            tuple[int, str, str],
            list[tuple[BattleInferredBuffInterval, str, float, str]],
        ] = {}
        reasons_by_interval: dict[str, list[str]] = {}
        accepted_by_interval: dict[str, set[str]] = {}
        hit_token = None if projection_memo is None else projection_memo.hit_token(hit)
        for interval in active:
            evaluation = (
                evaluate_interval_projection(hit, interval, channel_id)
                if projection_memo is None
                else projection_memo.evaluate_interval(hit_token, hit, interval, channel_id)
            )
            if evaluation.accepted_properties:
                accepted_by_interval.setdefault(interval.interval_id, set()).update(
                    evaluation.accepted_properties
                )
            reasons_by_interval[interval.interval_id] = list(evaluation.reasons)
            for property_id, value, confidence in evaluation.candidates:
                key = (
                    interval.source_character_id, interval.buff_asset_path,
                    f"{interval.target_scope}:{property_id}",
                )
                selected.setdefault(key, []).append((interval, property_id, value, confidence))

        grouped: dict[
            tuple[str, str],
            list[tuple[BattleInferredBuffInterval, float, str]],
        ] = {}
        for candidates in selected.values():
            if len(candidates) == 1:
                interval, property_id, value, confidence = candidates[0]
                stackable = (
                    "aggregatebysource" in interval.stacking_type.casefold()
                    or interval.stack_limit_count > 1
                )
                if stackable and interval.stack_limit_count <= 0:
                    continue
                applied_stacks = (
                    min(max(1, interval.stacks), interval.stack_limit_count)
                    if stackable else 1
                )
                grouped.setdefault((interval.target_scope, property_id), []).append(
                    (interval, value * applied_stacks, confidence)
                )
                continue
            ordered = sorted(
                candidates,
                key=lambda row: (row[0].start_us, row[0].interval_id),
                reverse=True,
            )
            stackable = any(
                "aggregatebysource" in row[0].stacking_type.casefold()
                or row[0].stack_limit_count > 1
                for row in ordered
            )
            retained: list[
                tuple[BattleInferredBuffInterval, str, float, str, int]
            ] = []
            if stackable:
                remaining = max(row[0].stack_limit_count for row in ordered)
                for interval, property_id, value, confidence in ordered:
                    if remaining <= 0:
                        break
                    applied_stacks = min(max(1, interval.stacks), remaining)
                    retained.append((
                        interval,
                        property_id,
                        value,
                        confidence,
                        applied_stacks,
                    ))
                    remaining -= applied_stacks
            elif ordered:
                interval, property_id, value, confidence = ordered[0]
                retained.append((interval, property_id, value, confidence, 1))
            for interval, property_id, value, confidence, applied_stacks in retained:
                grouped.setdefault(
                    (interval.target_scope, property_id),
                    [],
                ).append((interval, value * applied_stacks, confidence))
        projected = tuple(
            BattleProjectedBuffModifier(
                property_id=property_id,
                additive_value=sum(row[1] for row in rows),
                interval_ids=tuple(row[0].interval_id for row in rows),
                buff_names=tuple(dict.fromkeys(row[0].buff_name for row in rows)),
                confidence=_minimum_confidence(*(row[2] for row in rows)),
                target_scope=target_scope,
            )
            for (target_scope, property_id), rows in sorted(grouped.items())
        )
        applied_ids = tuple(dict.fromkeys(
            interval_id
            for modifier in projected
            for interval_id in modifier.interval_ids
        ))
        applied_set = set(applied_ids)
        decisions = []
        for interval in active:
            interval_id = interval.interval_id
            interval_reasons = list(reasons_by_interval.get(interval_id, ()))
            if interval_id in applied_set:
                status = "applied"
            elif interval_id in accepted_by_interval:
                status = "not_applied"
                interval_reasons.append("同一来源与属性的更新区间已覆盖该证据")
            elif any(
                marker in reason
                for reason in interval_reasons
                for marker in _UNRESOLVED_REASON_MARKERS
            ):
                status = "unresolved"
            else:
                status = "not_applied"
            decisions.append(BattleBuffProjectionDecision(
                interval_id=interval_id,
                buff_name=interval.buff_name,
                status=status,
                applied_property_ids=tuple(sorted(
                    property_id
                    for property_id in accepted_by_interval.get(interval_id, ())
                    if interval_id in applied_set
                )),
                reasons=tuple(dict.fromkeys(interval_reasons)),
            ))
        excluded_ids = tuple(
            row.interval_id for row in decisions if row.status != "applied"
        )
        exclusion_reasons = tuple(dict.fromkeys(
            reason
            for row in decisions
            if row.status != "applied"
            for reason in row.reasons
        ))
        projection = BattleHitBuffProjection(
            event_id=hit.event_id,
            modifiers=projected,
            applied_interval_ids=applied_ids,
            excluded_interval_ids=excluded_ids,
            exclusion_reasons=exclusion_reasons,
            confidence=_minimum_confidence(*(row.confidence for row in projected)),
            decisions=tuple(decisions),
        )
        return BattleForkHitAdjustmentService.adjust_projection(
            hit,
            temporal,
            projection,
        )

    @staticmethod
    def apply_additive(
        values: Mapping[str, float],
        projection: BattleHitBuffProjection,
    ) -> dict[str, float]:
        """Return a copy with safe dynamic modifiers applied exactly once."""

        result = {str(key): float(value) for key, value in values.items()}
        for modifier in projection.modifiers:
            if (
                modifier.target_scope not in {"self", "team", "team_others"}
                and not modifier.target_scope.startswith("character:")
            ):
                continue
            result[modifier.property_id] = (
                result.get(modifier.property_id, 0.0) + modifier.additive_value
            )
        return result
