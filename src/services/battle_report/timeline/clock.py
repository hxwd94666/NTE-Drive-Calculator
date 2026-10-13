# 战报轴在“包含时停”和“扣除时停”之间进行可逆时间投影。
"""Pure timeline projection helpers for battle-report presentation."""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from typing import Literal


BattleTimelineTimeMode = Literal["elapsed", "active"]

ELAPSED_TIME_MODE: BattleTimelineTimeMode = "elapsed"
ACTIVE_TIME_MODE: BattleTimelineTimeMode = "active"


def merge_time_stop_intervals(
    intervals: Iterable[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    # 只合并既有整型区间；clip/unknown过滤由调用者保持原有责任。
    merged: list[tuple[int, int]] = []
    for start_us, end_us in sorted(intervals):
        if not merged or start_us > merged[-1][1]:
            merged.append((start_us, end_us))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end_us))
    return tuple(merged)


def normalized_time_stop_intervals(
    intervals: Iterable[tuple[int | None, int | None]],
    *,
    battle_start_us: int,
    battle_end_us: int,
) -> tuple[tuple[int, int], ...]:
    """Clip, sort and merge usable stop intervals inside one battle axis."""

    clipped = (
        (
            max(battle_start_us, int(start_us)),
            min(battle_end_us, int(end_us)),
        )
        for start_us, end_us in intervals
        if start_us is not None
        and end_us is not None
        and int(end_us) > int(start_us)
        and int(end_us) > battle_start_us
        and int(start_us) < battle_end_us
    )
    return merge_time_stop_intervals(clipped)


def time_stop_overlap_us(
    start_us: int,
    end_us: int,
    intervals: Iterable[tuple[int | None, int | None]],
) -> int:
    """Return merged stop duration intersecting the half-open range."""

    if end_us <= start_us:
        return 0
    frozen_intervals = tuple(
        (start, end)
        for start, end in intervals
    )
    return _cached_time_stop_overlap_us(start_us, end_us, frozen_intervals)


@lru_cache(maxsize=16_384)
def _cached_time_stop_overlap_us(
    start_us: int,
    end_us: int,
    intervals: tuple[tuple[int | None, int | None], ...],
) -> int:
    """Reuse overlap projections repeated by each Buff counterfactual replay."""

    normalized = normalized_time_stop_intervals(
        intervals,
        battle_start_us=start_us,
        battle_end_us=end_us,
    )
    return sum(end - start for start, end in normalized)


def project_timeline_time_us(
    raw_time_us: int,
    *,
    battle_start_us: int,
    intervals: Iterable[tuple[int | None, int | None]],
    mode: BattleTimelineTimeMode,
) -> int:
    """Project raw elapsed time to the selected axis clock."""

    raw = max(battle_start_us, int(raw_time_us))
    if mode == ELAPSED_TIME_MODE:
        return raw - battle_start_us
    stopped = time_stop_overlap_us(battle_start_us, raw, intervals)
    return max(0, raw - battle_start_us - stopped)


def projected_range_duration_us(
    start_us: int,
    end_us: int,
    *,
    intervals: Iterable[tuple[int | None, int | None]],
    mode: BattleTimelineTimeMode,
) -> int:
    """Return range duration under the selected clock semantics."""

    raw_duration = max(0, int(end_us) - int(start_us))
    if mode == ELAPSED_TIME_MODE:
        return raw_duration
    return max(0, raw_duration - time_stop_overlap_us(start_us, end_us, intervals))


def unproject_timeline_time_us(
    display_time_us: int,
    *,
    battle_start_us: int,
    battle_end_us: int,
    intervals: Iterable[tuple[int | None, int | None]],
    mode: BattleTimelineTimeMode,
    prefer_interval_end: bool = False,
) -> int:
    """Map a displayed clock value back through the merged stop intervals.

    Active time has a plateau across each stop. Range starts choose the front
    edge of a plateau while range ends choose its back edge so drag selection
    does not silently discard evidence adjacent to a time stop.
    """

    start = int(battle_start_us)
    end = max(start, int(battle_end_us))
    if mode == ELAPSED_TIME_MODE:
        return min(end, max(start, start + int(display_time_us)))
    normalized = normalized_time_stop_intervals(
        intervals,
        battle_start_us=start,
        battle_end_us=end,
    )
    maximum = end - start - sum(stop_end - stop_start for stop_start, stop_end in normalized)
    target = min(maximum, max(0, int(display_time_us)))
    stopped = 0
    for stop_start, stop_end in normalized:
        plateau = stop_start - start - stopped
        if target < plateau:
            return start + target + stopped
        if target == plateau:
            # 活动时钟平顶有两个raw端点：范围起点取前沿，终点取后沿。
            return stop_end if prefer_interval_end else stop_start
        stopped += stop_end - stop_start
    return min(end, start + target + stopped)
