# 整批调度冻结 Buff 规则的触发和区间纯计算，保留 Python 差分依据。
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

from src.services.battle_report.native.state_payload import HIT_FIELDS, ACTION_FIELDS, RULE_FIELDS, state_rows, finalize_rows
from src.domain.battle_report.buffs.models import BattleStaticBuffRule
from src.domain.battle_report.timeline.events import BattleAnalysisHit, BattleInferredAction
from src.domain.battle_report.buffs.models import BattleInferredBuffInterval
from src.domain.battle_report.analysis.backend_contracts import BattleComputeBackend
from src.services.battle_report.buffs.interval_support import (
    BattleBuffIntervalSupportMixin, BuffOccurrence,
)


def compute_rule_intervals(
    rules: Sequence[BattleStaticBuffRule],
    *,
    actions: Sequence[BattleInferredAction],
    hits: Sequence[BattleAnalysisHit],
    battle_end_us: int,
    time_stop_intervals: Sequence[tuple[int | None, int | None]],
    backend: BattleComputeBackend | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> dict[int, tuple[tuple[BuffOccurrence, int], ...]]:
    """Calculate all rule intervals once; the key belongs to this request only."""
    if checkpoint is not None:
        checkpoint()
    if isinstance(backend, BattleComputeBackend) and backend.supports_battle_compute:
        payload = {
            "rules": state_rows(rules, RULE_FIELDS),
            "actions": state_rows(actions, ACTION_FIELDS),
            "hits": state_rows(hits, HIT_FIELDS),
            "battle_end_us": battle_end_us,
            "time_stop_intervals": list(time_stop_intervals),
        }
        responses = backend.compute_batch(
            "buff_rule_intervals_v1", (payload,), checkpoint=checkpoint,
        )
        if len(responses) != 1:
            raise ValueError("invalid_buff_state_result")
        rows = responses[0].get("rule_intervals")
        if not isinstance(rows, list) or len(rows) != len(rules):
            raise ValueError("invalid_buff_state_result")
        parsed = {}
        for rule, intervals in zip(rules, rows, strict=True):
            if checkpoint is not None:
                checkpoint()
            parsed[id(rule)] = _parse_intervals(intervals)
        return parsed
    occurrences = []
    removals: dict[tuple[int, str, str], list[BuffOccurrence]] = {}
    for rule in rules:
        if checkpoint is not None:
            checkpoint()
        rows = BattleBuffIntervalSupportMixin._occurrences(
            rule, actions=actions, hits=hits, battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        )
        occurrences.append(rows)
        if "remove" in rule.effect_type.casefold():
            key = (
                rule.source_character_id, rule.source_effect_definition_id,
                rule.target_asset_path,
            )
            removals.setdefault(key, []).extend(rows)
    for remove_rows in removals.values():
        remove_rows.sort(key=lambda row: row.time_us)
    result = {}
    for rule, rows in zip(rules, occurrences, strict=True):
        if checkpoint is not None:
            checkpoint()
        key = (
            rule.source_character_id, rule.source_effect_definition_id,
            rule.target_asset_path,
        )
        result[id(rule)] = (
            () if "remove" in rule.effect_type.casefold()
            else BattleBuffIntervalSupportMixin._occurrence_ends(
                rule, rows, removals.get(key, ()), battle_end_us, time_stop_intervals,
            )
        )
    return result


def _parse_intervals(value: object) -> tuple[tuple[BuffOccurrence, int], ...]:
    if not isinstance(value, list):
        raise ValueError("invalid_buff_state_result")
    result = []
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("invalid_buff_state_result")
        row, end = pair
        if not isinstance(row, dict) or type(end) is not int:
            raise ValueError("invalid_buff_state_result")
        start, confidence = row.get("time_us"), row.get("state_confidence")
        actions, events, target = (
            row.get("action_ids"), row.get("event_ids"), row.get("target_id"),
        )
        if (
            type(start) is not int or end <= start
            or confidence not in ("低", "中", "高")
            or not isinstance(target, str)
            or not isinstance(actions, list) or not isinstance(events, list)
            or not all(isinstance(item, str) for item in (*actions, *events))
        ):
            raise ValueError("invalid_buff_state_result")
        result.append((BuffOccurrence(
            start, confidence, tuple(actions), tuple(events), target,
        ), end))
    return tuple(result)


def finalize_buff_intervals(
    intervals: Sequence[BattleInferredBuffInterval],
    *,
    confirmed_all_boss: bool,
    backend: BattleComputeBackend | None,
    checkpoint: Callable[[], None] | None,
) -> tuple[BattleInferredBuffInterval, ...] | None:
    if not isinstance(backend, BattleComputeBackend) or not backend.supports_battle_compute:
        return None
    if checkpoint:
        checkpoint()
    responses = backend.compute_batch("buff_interval_finalize_v1", ({
        "intervals": finalize_rows(intervals),
        "confirmed_all_boss": confirmed_all_boss,
    },), checkpoint=checkpoint)
    if len(responses) != 1:
        raise ValueError("invalid_buff_finalize_result")
    rows = responses[0].get("intervals")
    if not isinstance(rows, list) or len(rows) != len(intervals):
        raise ValueError("invalid_buff_finalize_result")
    result, seen = [], set()
    for row in rows:
        if checkpoint:
            checkpoint()
        if not isinstance(row, dict):
            raise ValueError("invalid_buff_finalize_result")
        index, end, tags, consumed = (
            row.get("index"), row.get("end_us"), row.get("target_require_tags"),
            row.get("boss_requirement_consumed"),
        )
        if (
            type(index) is not int or not 0 <= index < len(intervals) or index in seen
            or type(end) is not int or type(consumed) is not bool
            or not isinstance(tags, list) or len(tags) != len(intervals[index].modifiers)
            or not all(isinstance(group, list) and all(isinstance(tag, str) for tag in group)
                       for group in tags)
        ):
            raise ValueError("invalid_buff_finalize_result")
        seen.add(index)
        interval = intervals[index]
        result.append(replace(
            interval, end_us=end,
            modifiers=tuple(replace(modifier, target_require_tags=tuple(group))
                            for modifier, group in zip(interval.modifiers, tags, strict=True)),
            inference_basis=(interval.inference_basis
                             + " 正式怪物目录已确认当前解析目标均为 Boss。"
                             if consumed else interval.inference_basis),
        ))
    return tuple(result)
