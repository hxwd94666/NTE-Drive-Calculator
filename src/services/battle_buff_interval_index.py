# Buff 区间的不可变逐击查询索引，避免每击线性扫描全部区间。
"""Immutable interval index for per-hit inferred Buff queries."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import overload

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredBuffInterval,
)


@dataclass(frozen=True, slots=True)
class _IndexedInterval:
    ordinal: int
    interval: BattleInferredBuffInterval


@dataclass(frozen=True, slots=True)
class _IntervalNode:
    center_us: int
    by_start: tuple[_IndexedInterval, ...]
    by_end_descending: tuple[_IndexedInterval, ...]
    left: _IntervalNode | None
    right: _IntervalNode | None


def buff_interval_applies_to_hit(
    interval: BattleInferredBuffInterval,
    hit: BattleAnalysisHit,
) -> bool:
    """Return the legacy temporal and recipient-scope decision for one hit."""

    if not (
        interval.start_us <= hit.relative_time_us < interval.end_us
    ):
        return False
    return (
        interval.target_scope == "team"
        or (
            interval.target_scope == "team_others"
            and interval.source_character_id != hit.character_id
        )
        or (
            interval.target_scope == "self"
            and interval.source_character_id == hit.character_id
        )
        or interval.target_scope == f"character:{hit.character_id}"
        or interval.target_scope in {"target", "unknown"}
    )


def _build_node(
    entries: tuple[_IndexedInterval, ...],
) -> _IntervalNode | None:
    if not entries:
        return None
    centers = sorted(
        row.interval.start_us
        + (row.interval.end_us - row.interval.start_us) // 2
        for row in entries
    )
    center_us = centers[len(centers) // 2]
    left: list[_IndexedInterval] = []
    right: list[_IndexedInterval] = []
    overlapping: list[_IndexedInterval] = []
    for row in entries:
        interval = row.interval
        if interval.end_us <= center_us:
            left.append(row)
        elif interval.start_us > center_us:
            right.append(row)
        else:
            overlapping.append(row)
    return _IntervalNode(
        center_us=center_us,
        by_start=tuple(sorted(
            overlapping,
            key=lambda row: (row.interval.start_us, row.ordinal),
        )),
        by_end_descending=tuple(sorted(
            overlapping,
            key=lambda row: (-row.interval.end_us, row.ordinal),
        )),
        left=_build_node(tuple(left)),
        right=_build_node(tuple(right)),
    )


class BattleBuffIntervalIndex(Sequence[BattleInferredBuffInterval]):
    """Index one complete frozen interval collection without changing its order."""

    __slots__ = ("_intervals", "_root")

    def __init__(
        self,
        intervals: Sequence[BattleInferredBuffInterval],
    ) -> None:
        self._intervals = tuple(intervals)
        # Empty/reversed intervals can never satisfy the legacy half-open test.
        eligible = tuple(
            _IndexedInterval(ordinal, interval)
            for ordinal, interval in enumerate(self._intervals)
            if interval.start_us < interval.end_us
        )
        self._root = _build_node(eligible)

    @overload
    def __getitem__(self, index: int) -> BattleInferredBuffInterval:
        ...

    @overload
    def __getitem__(
        self,
        index: slice,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        ...

    def __getitem__(
        self,
        index: int | slice,
    ) -> BattleInferredBuffInterval | tuple[BattleInferredBuffInterval, ...]:
        return self._intervals[index]

    def __iter__(self) -> Iterator[BattleInferredBuffInterval]:
        return iter(self._intervals)

    def __len__(self) -> int:
        return len(self._intervals)

    @property
    def intervals(self) -> tuple[BattleInferredBuffInterval, ...]:
        """Return the complete, original-order frozen collection."""

        return self._intervals

    def active_for_hit(
        self,
        hit: BattleAnalysisHit,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        """Return legacy-equivalent active intervals in original input order."""

        return tuple(
            interval
            for interval in self.temporal_for_hit(hit)
            if buff_interval_applies_to_hit(interval, hit)
        )

    def temporal_for_hit(
        self,
        hit: BattleAnalysisHit,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        """Return time-overlapping intervals before recipient-scope filtering."""

        temporal: list[_IndexedInterval] = []
        node = self._root
        time_us = hit.relative_time_us
        while node is not None:
            if time_us < node.center_us:
                for row in node.by_start:
                    if row.interval.start_us > time_us:
                        break
                    temporal.append(row)
                node = node.left
            elif time_us > node.center_us:
                for row in node.by_end_descending:
                    if row.interval.end_us <= time_us:
                        break
                    temporal.append(row)
                node = node.right
            else:
                temporal.extend(node.by_start)
                break
        return tuple(
            row.interval
            for row in sorted(temporal, key=lambda row: row.ordinal)
        )

    def excluding(
        self,
        interval_ids: frozenset[str],
    ) -> "BattleBuffIntervalIndexView":
        return BattleBuffIntervalIndexView(self, interval_ids)


class BattleBuffIntervalIndexView(Sequence[BattleInferredBuffInterval]):
    """Filtered view sharing one immutable interval tree with its source."""

    __slots__ = ("_source", "_excluded", "_intervals")

    def __init__(
        self,
        source: BattleBuffIntervalIndex,
        excluded_interval_ids: frozenset[str],
    ) -> None:
        self._source = source
        self._excluded = excluded_interval_ids
        self._intervals: tuple[BattleInferredBuffInterval, ...] | None = None

    def _materialize(self) -> tuple[BattleInferredBuffInterval, ...]:
        intervals = self._intervals
        if intervals is None:
            intervals = tuple(
                interval
                for interval in self._source.intervals
                if interval.interval_id not in self._excluded
            )
            self._intervals = intervals
        return intervals

    @overload
    def __getitem__(self, index: int) -> BattleInferredBuffInterval:
        ...

    @overload
    def __getitem__(
        self,
        index: slice,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        ...

    def __getitem__(
        self,
        index: int | slice,
    ) -> BattleInferredBuffInterval | tuple[BattleInferredBuffInterval, ...]:
        return self._materialize()[index]

    def __iter__(self) -> Iterator[BattleInferredBuffInterval]:
        return iter(self._materialize())

    def __len__(self) -> int:
        return len(self._materialize())

    @property
    def intervals(self) -> tuple[BattleInferredBuffInterval, ...]:
        return self._materialize()

    def active_for_hit(
        self,
        hit: BattleAnalysisHit,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        return tuple(
            interval
            for interval in self._source.active_for_hit(hit)
            if interval.interval_id not in self._excluded
        )

    def temporal_for_hit(
        self,
        hit: BattleAnalysisHit,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        return tuple(
            interval
            for interval in self._source.temporal_for_hit(hit)
            if interval.interval_id not in self._excluded
        )


BattleBuffIntervalQuery = BattleBuffIntervalIndex | BattleBuffIntervalIndexView


__all__ = [
    "BattleBuffIntervalIndex",
    "BattleBuffIntervalIndexView",
    "BattleBuffIntervalQuery",
    "buff_interval_applies_to_hit",
]
