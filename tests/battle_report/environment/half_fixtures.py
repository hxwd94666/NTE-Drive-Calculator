# 共享半场角色证据与区间夹具，保持未知归属和原有边界输入。
from __future__ import annotations

from src.domain.battle_report import BattleInferredBuffInterval


def _interval(
    interval_id: str,
    character_id: int,
    start_us: int = 0,
    end_us: int = 100_000_000,
) -> BattleInferredBuffInterval:
    return BattleInferredBuffInterval(
        interval_id=interval_id,
        buff_asset_path=f"/Game/{interval_id}",
        buff_name=interval_id,
        source_effect_definition_id=interval_id,
        source_kind="test",
        source_character_id=character_id,
        source_character_name=str(character_id),
        target_scope="team",
        start_us=start_us,
        end_us=end_us,
        stacks=1,
        duration_policy="test",
        state_confidence="test",
        value_confidence="test",
        inference_basis="test",
        trigger_event_type="test",
        evidence_action_ids=(),
        evidence_event_ids=(),
        modifiers=(),
    )


def _hit(
    time_us: int,
    half: str,
    character_id: int,
    *,
    direction: str = "outgoing",
    damage: float = 1.0,
    character_known: bool = True,
) -> dict[str, object]:
    return {
        "relative_time_us": time_us,
        "abyss_half": half,
        "direction": direction,
        "damage": damage,
        "character_id": character_id,
        "character_known": character_known,
    }
