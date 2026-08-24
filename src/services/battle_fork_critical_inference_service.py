# 将第一轮逐击公式重放判定的暴击转换为糖果骑士叠层证据。
"""One-way replay-to-fork evidence adapter used for a bounded second pass."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.domain.battle_report import BattleAnalysisHit, BattleHitReplayResult
from src.services.battle_fork_default_policy import (
    INFER_KNIGHT_CANDY_CRITICAL_FROM_HIT_REPLAY,
)
from src.services.battle_fork_trigger_refinement_service import ForkCriticalEvent


FORK_CRITICAL_INFERENCE_MODEL_VERSION = "battle-fork-critical-inference-v1"


class BattleForkCriticalInferenceService:
    """Derive crit triggers once; inferred Buffs never feed this pass back again."""

    @staticmethod
    def infer(
        hits: Sequence[BattleAnalysisHit],
        replays: Sequence[BattleHitReplayResult],
        rules: Sequence[Any] | None = None,
    ) -> tuple[ForkCriticalEvent, ...]:
        if not INFER_KNIGHT_CANDY_CRITICAL_FROM_HIT_REPLAY:
            return ()
        if rules is not None and not any(
            "upgradestar_pack_fork_knightcandy"
            in str(row.source_effect_definition_id).casefold()
            for row in rules
        ):
            return ()
        hit_by_event = {row.event_id: row for row in hits}
        return tuple(
            ForkCriticalEvent(
                event_id=row.event_id,
                relative_time_us=hit_by_event[row.event_id].relative_time_us,
                source_character_id=int(hit_by_event[row.event_id].character_id),
                evidence_kind="hit_replay_inferred",
            )
            for row in replays
            if row.critical_state == "critical"
            and row.event_id in hit_by_event
            and hit_by_event[row.event_id].character_id is not None
        )
