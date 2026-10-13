# 将冻结逐击与状态配置送交既有计算协议，不拥有角色或环境渲染。
from __future__ import annotations


def compute_team_state(backend, kind, *, hits, battle_end_us, time_stop_intervals, checkpoint, **values):
    needs_channel = kind == "daffodill" and "Effect4" in values.get("effects", ())
    payload = {
        "kind": kind, "hits": [{
            "event_id": hit.event_id, "relative_time_us": hit.relative_time_us,
            "sequence": hit.sequence, "target_id": hit.target_id, "direction": hit.direction,
            "gameplay_effect_id": hit.gameplay_effect_id,
            "channel_input": {key: getattr(hit, key) for key in (
                "direction", "gameplay_effect_id", "classification", "is_follow_up",
                "damage_name", "damage_component", "attack_type", "ability_id", "skill_name",
            )} if needs_channel else {},
        } for hit in hits],
        "battle_end_us": battle_end_us, "time_stop_intervals": time_stop_intervals,
        "topple_duration_us": None, "topple_limit": None, "topple_recovery_speed": None, **values,
    }
    result = backend.compute_batch("team_state_v1", (payload,), checkpoint=checkpoint)
    if len(result) != 1 or not isinstance(result[0].get("intervals"), list):
        raise ValueError("Native team state result shape mismatch")
    return result[0]["intervals"]
