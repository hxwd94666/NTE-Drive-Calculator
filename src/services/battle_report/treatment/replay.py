# 编排来源侧治疗事件及其 Buff 消费者。
"""Orchestrate source-side treatment events and their Buff consumers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from src.domain.native_analysis import BattleComputeBackend
from src.services.battle_report.treatment.native import infer_treatment_batch

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredAction,
    BattleInferredBuffInterval,
    BattleTreatmentEvent,
)
from src.services.battle_report.treatment.mechanisms import BattleTreatmentBuffService
from src.services.battle_report.treatment.mechanisms import (
    TREATMENT_EVENT_MODEL_VERSION,
    BattleTreatmentEventService,
)


@dataclass(frozen=True, slots=True)
class BattleTreatmentReplayProjection:
    events: tuple[BattleTreatmentEvent, ...]
    buff_intervals: tuple[BattleInferredBuffInterval, ...]


class BattleTreatmentReplayService:
    """Build the treatment axis once, then feed its formal consumers."""

    @classmethod
    def infer(
        cls,
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
        state_buff_intervals: Sequence[BattleInferredBuffInterval] = (),
        zankou_effect_three_recover_ratio: float | None = None,
        infer_buffs: bool,
        backend: BattleComputeBackend | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> BattleTreatmentReplayProjection:
        if backend is not None and backend.supports_battle_compute:
            events, buff_intervals = infer_treatment_batch(
                build=build, actions=actions, hits=hits, battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
                state_buff_intervals=state_buff_intervals,
                zankou_effect_three_recover_ratio=zankou_effect_three_recover_ratio,
                infer_buffs=infer_buffs, backend=backend, checkpoint=checkpoint,
            )
            return BattleTreatmentReplayProjection(events=events, buff_intervals=buff_intervals)
        # 现有离线差分分支保持原义；正式分析页继续只经独立Core gate。
        events = BattleTreatmentEventService.infer(
            build=build,
            actions=actions,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
            state_buff_intervals=state_buff_intervals,
            zankou_effect_three_recover_ratio=(
                zankou_effect_three_recover_ratio
            ),
        )
        buff_intervals = (
            BattleTreatmentBuffService.infer(
                build=build,
                treatment_events=events,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
            )
            if infer_buffs
            else ()
        )
        return BattleTreatmentReplayProjection(
            events=events,
            buff_intervals=buff_intervals,
        )


__all__ = (
    "BattleTreatmentReplayProjection",
    "BattleTreatmentReplayService",
    "TREATMENT_EVENT_MODEL_VERSION",
)
