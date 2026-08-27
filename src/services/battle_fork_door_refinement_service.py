# 仅按来源侧正式治疗事件重放错误的门，不从技能动作猜治疗。
"""Door fork inference driven by the shared treatment-event axis."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import (
    BattleInferredAction,
    BattleInferredBuffInterval,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


DOOR_REFINEMENT_MODEL_VERSION = "battle-fork-door-refinement-v1"
_DOOR_EVENTS = frozenset({
    "FORK_DOOR_TREATMENT_SELF",
    "FORK_DOOR_TREATMENT_OTHERS",
})


@dataclass(slots=True)
class _Chain:
    start_us: int
    end_active_us: int
    action_ids: list[str]
    event_ids: list[str]


def _active_time(
    raw_time_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return project_timeline_time_us(
        raw_time_us,
        battle_start_us=0,
        intervals=intervals,
        mode=ACTIVE_TIME_MODE,
    )


def _expiry(
    active_time_us: int,
    *,
    battle_end_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return unproject_timeline_time_us(
        active_time_us,
        battle_start_us=0,
        battle_end_us=battle_end_us,
        intervals=intervals,
        mode=ACTIVE_TIME_MODE,
        prefer_interval_end=True,
    )


class BattleForkDoorRefinementService:
    """Infer Door intervals only from the shared formal treatment-event axis."""

    @classmethod
    def infer(
        cls,
        rules: Sequence[Any],
        *,
        actions: Sequence[BattleInferredAction],
        treatment_events: Sequence[Any],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        del actions
        results: list[BattleInferredBuffInterval] = []
        for rule in (row for row in rules if row.event_type in _DOOR_EVENTS):
            if rule.duration_seconds is None:
                continue
            formal = sorted(
                (
                    row for row in treatment_events
                    if row.source_character_id == rule.source_character_id
                ),
                key=lambda row: (row.relative_time_us, row.event_id),
            )
            occurrences = tuple(
                (row.relative_time_us, "", row.event_id) for row in formal
            )
            duration_us = round(rule.duration_seconds * 1_000_000)
            chains: list[_Chain] = []
            for raw_us, action_id, event_id in occurrences:
                now_active = _active_time(raw_us, time_stop_intervals)
                proposed_end = now_active + duration_us
                if chains and now_active < chains[-1].end_active_us:
                    chains[-1].end_active_us = proposed_end
                    if action_id:
                        chains[-1].action_ids.append(action_id)
                    if event_id:
                        chains[-1].event_ids.append(event_id)
                else:
                    chains.append(_Chain(
                        start_us=raw_us,
                        end_active_us=proposed_end,
                        action_ids=[action_id] if action_id else [],
                        event_ids=[event_id] if event_id else [],
                    ))
            for index, chain in enumerate(chains):
                end_us = min(battle_end_us, _expiry(
                    chain.end_active_us,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                ))
                if end_us <= chain.start_us:
                    continue
                results.append(BattleInferredBuffInterval(
                    interval_id=f"buff:fork:door:{index}:{rule.rule_id}",
                    buff_asset_path=rule.target_asset_path,
                    buff_name=rule.target_name,
                    source_effect_definition_id=rule.source_effect_definition_id,
                    source_kind=rule.source_kind,
                    source_character_id=rule.source_character_id,
                    source_character_name=rule.source_character_name,
                    target_scope=rule.target_scope,
                    start_us=chain.start_us,
                    end_us=end_us,
                    stacks=1,
                    duration_policy=rule.duration_policy,
                    state_confidence="中",
                    value_confidence="高",
                    inference_basis=(
                        "按统一来源侧治疗事件刷新；满血或零有效治疗仍可触发。"
                    ),
                    trigger_event_type=rule.event_type,
                    evidence_action_ids=tuple(chain.action_ids),
                    evidence_event_ids=tuple(chain.event_ids),
                    modifiers=rule.modifiers,
                    stacking_type=rule.stacking_type,
                    stack_limit_count=rule.stack_limit_count,
                ))
        return tuple(results)
