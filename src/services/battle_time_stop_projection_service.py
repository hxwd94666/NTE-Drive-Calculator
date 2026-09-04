# 将 nte-core 记录区间或 Q 动作低置信回退投影为统一有效战斗时钟。
"""Pure time-stop source selection for battle analysis."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from src.domain.battle_report import (
    BattleInferredAction,
    BattleObservedTimeStopInterval,
)


TIME_STOP_PROJECTION_MODEL_VERSION = "battle-time-stop-projection-v2"
Q_PAUSE_TYPE_MASK = (1 << 2) | (1 << 3) | (1 << 4)
LINKO_SELECTION_PAUSE_TYPE_MASK = 1 << 6


@dataclass(frozen=True, slots=True)
class BattleTimeStopProjection:
    intervals: tuple[tuple[int, int], ...]
    source_kind: str
    confidence: str
    inference_basis: str
    q_action_intervals: tuple[tuple[int, int], ...] = ()
    type6_intervals: tuple[tuple[int, int], ...] = ()
    non_type6_intervals: tuple[tuple[int, int], ...] = ()
    inferred_linko_e_intervals: tuple[tuple[int, int], ...] = ()
    has_unknown_types: bool = False


def _pause_type_mask(row: Mapping[str, Any]) -> int | None:
    value = row.get("pause_type_mask")
    raw = row.get("raw_interval")
    if value is None and isinstance(raw, Mapping):
        value = raw.get("pause_type_mask")
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= 0xFFFF_FFFF
    ):
        return None
    return value


def _relative_observed_interval(
    row: Mapping[str, Any],
    *,
    origin_us: int | None,
    contract_version: int,
) -> BattleObservedTimeStopInterval:
    pause_type_mask = _pause_type_mask(row)
    type_status = (
        "typed"
        if pause_type_mask is not None
        else "compacted_unknown"
        if contract_version >= 5
        else "legacy_unknown"
    )
    raw = row.get("raw_interval")
    if isinstance(raw, Mapping):
        start_offset = raw.get("start_offset_seconds")
        end_offset = raw.get("end_offset_seconds")
        if isinstance(start_offset, (int, float)) and isinstance(
            end_offset, (int, float)
        ):
            return BattleObservedTimeStopInterval(
                start_us=max(0, round(float(start_offset) * 1_000_000)),
                end_us=max(0, round(float(end_offset) * 1_000_000)),
                pause_type_mask=pause_type_mask,
                type_status=type_status,
            )
    if origin_us is None:
        return BattleObservedTimeStopInterval(
            None,
            None,
            pause_type_mask,
            type_status,
        )
    start_unix_us = row.get("start_unix_us")
    end_unix_us = row.get("end_unix_us")
    return BattleObservedTimeStopInterval(
        start_us=(
            None
            if start_unix_us is None
            else max(0, int(start_unix_us) - origin_us)
        ),
        end_us=(
            None
            if end_unix_us is None
            else max(0, int(end_unix_us) - origin_us)
        ),
        pause_type_mask=pause_type_mask,
        type_status=type_status,
    )


def _usable_intervals(
    intervals: Sequence[tuple[int | None, int | None]],
) -> tuple[tuple[int, int], ...]:
    return tuple(
        sorted(
            (int(start_us), int(end_us))
            for start_us, end_us in intervals
            if start_us is not None
            and end_us is not None
            and int(end_us) > int(start_us)
        )
    )


def _typed_intervals(
    intervals: Sequence[
        BattleObservedTimeStopInterval | tuple[int | None, int | None]
    ],
) -> tuple[BattleObservedTimeStopInterval, ...]:
    result = []
    for interval in intervals:
        if isinstance(interval, BattleObservedTimeStopInterval):
            result.append(
                replace(interval, type_status="typed")
                if interval.pause_type_mask is not None
                else interval
            )
        else:
            result.append(BattleObservedTimeStopInterval(
                interval[0], interval[1], None, "legacy_unknown"
            ))
    return tuple(result)


def _interval_pairs(
    intervals: Sequence[BattleObservedTimeStopInterval],
    *,
    predicate: Callable[[BattleObservedTimeStopInterval], bool],
) -> tuple[tuple[int, int], ...]:
    return _merge_intervals(tuple(
        (int(interval.start_us), int(interval.end_us))
        for interval in intervals
        if interval.start_us is not None
        and interval.end_us is not None
        and int(interval.end_us) > int(interval.start_us)
        and predicate(interval)
    ))


def _merge_intervals(
    intervals: Sequence[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    merged: list[tuple[int, int]] = []
    for start_us, end_us in sorted(intervals):
        if not merged or start_us > merged[-1][1]:
            merged.append((start_us, end_us))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end_us))
    return tuple(merged)


class BattleTimeStopProjectionService:
    """Prefer measured intervals and fall back to inferred Q action windows."""

    @staticmethod
    def observed_intervals(
        rows: Sequence[Mapping[str, Any]],
        *,
        origin_us: int | None,
        contract_version: int = 4,
    ) -> tuple[tuple[int | None, int | None], ...]:
        return tuple(
            (interval.start_us, interval.end_us)
            for interval in BattleTimeStopProjectionService.observed_typed_intervals(
                rows,
                origin_us=origin_us,
                contract_version=contract_version,
            )
        )

    @staticmethod
    def observed_typed_intervals(
        rows: Sequence[Mapping[str, Any]],
        *,
        origin_us: int | None,
        contract_version: int = 4,
    ) -> tuple[BattleObservedTimeStopInterval, ...]:
        return tuple(
            _relative_observed_interval(
                row,
                origin_us=origin_us,
                contract_version=contract_version,
            )
            for row in rows
        )

    @staticmethod
    def q_action_intervals(
        observed_intervals: Sequence[
            BattleObservedTimeStopInterval | tuple[int | None, int | None]
        ],
    ) -> tuple[tuple[int, int], ...]:
        typed = _typed_intervals(observed_intervals)
        return _interval_pairs(
            typed,
            predicate=lambda interval: (
                interval.type_status == "legacy_unknown"
                or interval.pause_type_mask is not None
                and bool(interval.pause_type_mask & Q_PAUSE_TYPE_MASK)
            ),
        )

    @staticmethod
    def with_inferred_linko_e(
        projection: BattleTimeStopProjection,
        intervals: Sequence[tuple[int | None, int | None]],
    ) -> BattleTimeStopProjection:
        inferred = _merge_intervals(_usable_intervals(intervals))
        if not inferred:
            return projection
        source_kind = {
            "none": "inferred_linko_e",
            "inferred_q_action": "inferred_q_and_linko_e",
            "nte_core": "nte_core_plus_inferred_linko_e",
        }.get(projection.source_kind, projection.source_kind)
        return replace(
            projection,
            intervals=_merge_intervals((*projection.intervals, *inferred)),
            source_kind=source_kind,
            confidence="低",
            inference_basis=(
                f"{projection.inference_basis} 旧战报另按完整灵可 E、静态结束点"
                "与窗口内最早唯一同目标队友 QTE 推算低置信选人暂停；不写回"
                "Core 原始证据。"
            ),
            inferred_linko_e_intervals=inferred,
        )

    @staticmethod
    def resolve(
        observed_intervals: Sequence[
            BattleObservedTimeStopInterval | tuple[int | None, int | None]
        ],
        actions: Sequence[BattleInferredAction],
    ) -> BattleTimeStopProjection:
        typed = _typed_intervals(observed_intervals)
        observed = _usable_intervals(
            tuple((interval.start_us, interval.end_us) for interval in typed)
        )
        if observed:
            q_action_intervals = BattleTimeStopProjectionService.q_action_intervals(
                typed
            )
            type6_intervals = _interval_pairs(
                typed,
                predicate=lambda interval: interval.pause_type_mask is not None
                and bool(
                    interval.pause_type_mask & LINKO_SELECTION_PAUSE_TYPE_MASK
                ),
            )
            non_type6_intervals = _interval_pairs(
                typed,
                predicate=lambda interval: (
                    interval.type_status != "typed"
                    or interval.pause_type_mask is not None
                    and not bool(
                        interval.pause_type_mask
                        & LINKO_SELECTION_PAUSE_TYPE_MASK
                    )
                ),
            )
            has_unknown_types = any(
                interval.type_status != "typed"
                and interval.start_us is not None
                and interval.end_us is not None
                and interval.end_us > interval.start_us
                for interval in typed
            )
            return BattleTimeStopProjection(
                intervals=_merge_intervals(observed),
                source_kind="nte_core",
                confidence="高",
                inference_basis=(
                    "采用 nte-core 战报记录的时停起止点；该来源证明时钟暂停，"
                    "已知 Q 类型仅用于 Q 动作锚定，type6 仅作为灵可 E/QTE 的"
                    "辅助证据；只有旧记录缺少类型时保持旧 Q 锚定兼容，压缩"
                    "未知区间只参与有效时钟，不伪造具体类型。"
                ),
                q_action_intervals=q_action_intervals,
                type6_intervals=type6_intervals,
                non_type6_intervals=non_type6_intervals,
                has_unknown_types=has_unknown_types,
            )
        inferred = _merge_intervals(
            tuple(
                (int(action.start_us), int(action.end_us))
                for action in actions
                if action.input_kind == "Q" and action.end_us > action.start_us
            )
        )
        if inferred:
            return BattleTimeStopProjection(
                intervals=inferred,
                source_kind="inferred_q_action",
                confidence="低",
                inference_basis=(
                    "战报没有可用的 nte-core 时停起止点；按已推算 Q 动作的开始与结束"
                    "区间回退，并合并重叠区间。该区间不是实测时停。"
                ),
                q_action_intervals=inferred,
                non_type6_intervals=inferred,
            )
        return BattleTimeStopProjection(
            intervals=(),
            source_kind="none",
            confidence="",
            inference_basis="战报没有 nte-core 时停起止点，也没有完整的 Q 动作窗口。",
        )
