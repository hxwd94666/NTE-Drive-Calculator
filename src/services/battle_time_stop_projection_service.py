# 将正式时停证据或 Q 动作低置信回退投影为统一有效战斗时钟。
"""Pure time-stop source selection for battle analysis."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import BattleInferredAction


TIME_STOP_PROJECTION_MODEL_VERSION = "battle-time-stop-projection-v1"


@dataclass(frozen=True, slots=True)
class BattleTimeStopProjection:
    intervals: tuple[tuple[int, int], ...]
    source_kind: str
    confidence: str
    inference_basis: str


def _relative_observed_interval(
    row: Mapping[str, Any],
    *,
    origin_us: int | None,
) -> tuple[int | None, int | None]:
    raw = row.get("raw_interval")
    if isinstance(raw, Mapping):
        start_offset = raw.get("start_offset_seconds")
        end_offset = raw.get("end_offset_seconds")
        if isinstance(start_offset, (int, float)) and isinstance(
            end_offset, (int, float)
        ):
            return (
                max(0, round(float(start_offset) * 1_000_000)),
                max(0, round(float(end_offset) * 1_000_000)),
            )
    if origin_us is None:
        return None, None
    start_unix_us = row.get("start_unix_us")
    end_unix_us = row.get("end_unix_us")
    return (
        None if start_unix_us is None else max(0, int(start_unix_us) - origin_us),
        None if end_unix_us is None else max(0, int(end_unix_us) - origin_us),
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
    ) -> tuple[tuple[int | None, int | None], ...]:
        return tuple(
            _relative_observed_interval(row, origin_us=origin_us) for row in rows
        )

    @staticmethod
    def resolve(
        observed_intervals: Sequence[tuple[int | None, int | None]],
        actions: Sequence[BattleInferredAction],
    ) -> BattleTimeStopProjection:
        observed = _usable_intervals(observed_intervals)
        if observed:
            return BattleTimeStopProjection(
                intervals=observed,
                source_kind="nte_core",
                confidence="高",
                inference_basis="采用 nte-core 战报记录的正式时停起止点。",
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
                    "战报没有可用的正式时停起止点；按已推算 Q 动作的开始与结束"
                    "区间回退，并合并重叠区间。该区间不是实测时停。"
                ),
            )
        return BattleTimeStopProjection(
            intervals=(),
            source_kind="none",
            confidence="",
            inference_basis="战报没有正式时停起止点，也没有完整的 Q 动作窗口。",
        )
