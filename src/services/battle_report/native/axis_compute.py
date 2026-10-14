# 传递冻结轴与动画事实，并恢复原生推断的动作和伤害分组展示字段。
from __future__ import annotations

from src.domain.battle_report.timeline.events import BattleInferredAction, BattleTimelineDamageGroup
from src.services.battle_report.damage.composition import classify_battle_hit_channel
from src.services.official_role.skill_names import preferred_battle_damage_name


def serialize_animation_candidate(candidate):
    """只传原生 Candidate 字段，冻结的嵌套元组可直接交给 JSON 编码。"""
    return {
        "ability_id": candidate.ability_id,
        "selector_key": candidate.selector_key,
        "effect_hit_offsets_us": candidate.effect_hit_offsets_us,
        "trigger_end_offsets_us": candidate.trigger_end_offsets_us,
        "end_event_offsets_us": candidate.end_event_offsets_us,
        "section_end_offsets_us": candidate.section_end_offsets_us,
        "duration_us": candidate.duration_us,
        "hold_damage_mode": candidate.hold_damage_mode,
        "hold_prelude_us": candidate.hold_prelude_us,
    }


def _axis_hit(hit):
    return {
        "sequence": hit.sequence, "relative_time_us": hit.relative_time_us,
        "character_id": hit.character_id, "character_name": hit.character_name,
        "direction": hit.direction, "is_follow_up": hit.is_follow_up,
        "classification": hit.classification, "ability_id": hit.ability_id,
        "gameplay_effect_id": hit.gameplay_effect_id, "attack_type": hit.attack_type,
        "skill_name": hit.skill_name, "damage_name": hit.damage_name, "damage": hit.damage,
    }


def infer_native_actions(backend, hits, *, time_stop_intervals, animation_candidates,
                         checkpoint=None):
    payload = {
        "hits": [{**_axis_hit(hit), "event_id": hit.event_id} for hit in hits],
        "time_stop_intervals": list(time_stop_intervals),
        "animation_candidates": [serialize_animation_candidate(candidate) for candidate in animation_candidates],
    }
    response = backend.compute_batch("action_inference_v1", (payload,), checkpoint=checkpoint)
    if len(response) != 1:
        raise ValueError("Native action response count mismatch")
    return tuple(BattleInferredAction(**{
        **row, "evidence_event_ids": tuple(row["evidence_event_ids"]),
        "gameplay_effect_ids": tuple(row["gameplay_effect_ids"]),
    }) for row in response[0]["actions"])


def group_native_damage_hits(backend, hits, *, checkpoint=None):
    response = backend.compute_batch(
        "timeline_groups_v1", ({"hits": [
            {**_axis_hit(hit), "damage_component": hit.damage_component} for hit in hits
        ]},), checkpoint=checkpoint,
    )
    if len(response) != 1:
        raise ValueError("Native timeline response count mismatch")
    groups = []
    for ordinal, row in enumerate(response[0]["groups"]):
        if checkpoint is not None and ordinal % 64 == 0:
            checkpoint()
        # 原生索引及分组通道须与冻结逐击一致，未知/越界响应直接终止。
        indices = row["indices"]
        if not indices or any(type(index) is not int or not 0 <= index < len(hits) for index in indices):
            raise ValueError("Native timeline hit index mismatch")
        first = hits[indices[0]]
        channel, label = classify_battle_hit_channel(first)
        if channel != row["channel_key"]:
            raise ValueError("Native timeline channel mismatch")
        groups.append(BattleTimelineDamageGroup(
            group_id=f"damage-group:{first.sequence}:{ordinal}",
            character_id=first.character_id, character_name=first.character_name,
            direction=first.direction, channel_key=channel, channel_label=label,
            damage_name=preferred_battle_damage_name(first.damage_name, first.skill_name, first.ability_id),
            source_skill_name=first.skill_name, ability_id=first.ability_id,
            start_us=row["start_us"], end_us=row["end_us"], hits=len(indices),
            damage=row["damage"], evidence_event_ids=tuple(hits[index].event_id for index in indices),
        ))
    return tuple(groups)
