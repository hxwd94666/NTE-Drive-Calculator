"""Orchestrate source-side treatment events and their Buff consumers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredAction,
    BattleInferredBuffInterval,
    BattleTreatmentEvent,
)
from src.services.battle_treatment_buff_service import BattleTreatmentBuffService
from src.services.battle_treatment_event_service import (
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
    ) -> BattleTreatmentReplayProjection:
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
