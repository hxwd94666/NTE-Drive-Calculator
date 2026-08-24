# 将正式逐击和动作推断投影为统一时间轴的输入、技能条与伤害点分组。
"""Qt-free unified battle timeline projections."""

from __future__ import annotations

from collections.abc import Sequence

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredAction,
    BattleInferredInput,
    BattleTimelineDamageGroup,
)
from src.services.battle_damage_composition_service import (
    classify_battle_hit_channel,
)
from src.services.skill_name_rendering_service import preferred_battle_damage_name


TIMELINE_PROJECTION_MODEL_VERSION = "battle-unified-timeline-v5"

_GROUP_GAP_US = {
    "direct": 900_000,
    "direct_follow_up": 1_200_000,
    "reaction_hexed": 1_500_000,
    "reaction_unknown": 2_000_000,
    "reaction_creation": 2_000_000,
    "reaction_remora": 2_000_000,
    "reaction_nova": 2_000_000,
    "reaction_scorch": 2_000_000,
    "reaction_stain": 2_000_000,
    "reaction_charge": 2_000_000,
    "reaction_discord": 2_000_000,
    "special_nightmare": 2_000_000,
    "special_zankou_erosion": 2_000_000,
    "special_zankou_venom": 2_000_000,
}


def _input_projection(action: BattleInferredAction) -> BattleInferredInput:
    kind = action.input_kind
    if kind == "A":
        device = "mouse"
        text = "Z" if action.input_gesture == "hold" else "A"
        is_switch = False
    elif kind == "QTE":
        device = "keyboard"
        text = ""
        is_switch = True
    else:
        device = "keyboard"
        text = kind or "组合输入未知"
        is_switch = False
    return BattleInferredInput(
        input_event_id=f"input:{action.action_id}",
        action_id=action.action_id,
        device_kind=device,
        display_text=text,
        character_id=action.character_id,
        character_name=action.character_name,
        start_us=(
            action.input_start_us
            if action.input_gesture == "hold"
            and action.input_start_us is not None
            else action.start_us
        ),
        end_us=(
            action.input_end_us
            if action.input_gesture == "hold"
            and action.input_end_us is not None
            else action.end_us
            if action.input_gesture == "hold"
            else action.start_us + 1
        ),
        is_character_switch=is_switch,
        timing_confidence=action.timing_confidence,
        hold_damage_mode=action.hold_damage_mode,
    )


def _damage_source_key(hit: BattleAnalysisHit) -> str:
    ability = hit.ability_id.casefold()
    effect = hit.gameplay_effect_id.casefold()
    if ability == "ga_lacrimosa_skill" and "steal" in effect:
        return f"{hit.ability_id}|{hit.gameplay_effect_id}"
    if ability in {"ga_lacrimosa_melee", "ga_lacrimosa_ultraskill"}:
        mode = "mod_b" if "modb" in effect or "lacrimosa_b_" in effect else "mod_a"
        return f"{hit.ability_id}|{mode}"
    if hit.ability_id:
        return hit.ability_id
    if hit.skill_name and "未识别" not in hit.skill_name:
        return hit.skill_name
    return f"{hit.skill_name}|{hit.damage_name}|{hit.gameplay_effect_id}"


def _group_key(hit: BattleAnalysisHit) -> tuple[object, ...]:
    channel_key, _channel_label = classify_battle_hit_channel(hit)
    return (
        hit.character_id,
        hit.character_name,
        hit.direction,
        channel_key,
        _damage_source_key(hit),
    )


def _build_group(
    ordinal: int,
    rows: Sequence[BattleAnalysisHit],
) -> BattleTimelineDamageGroup:
    first = rows[0]
    last = rows[-1]
    channel_key, channel_label = classify_battle_hit_channel(first)
    damage_name = preferred_battle_damage_name(
        first.damage_name,
        first.skill_name,
        first.ability_id,
    )
    return BattleTimelineDamageGroup(
        group_id=f"damage-group:{first.sequence}:{ordinal}",
        character_id=first.character_id,
        character_name=first.character_name,
        direction=first.direction,
        channel_key=channel_key,
        channel_label=channel_label,
        damage_name=damage_name,
        source_skill_name=first.skill_name,
        ability_id=first.ability_id,
        start_us=first.relative_time_us,
        end_us=max(first.relative_time_us + 1, last.relative_time_us + 1),
        hits=len(rows),
        damage=sum(row.damage for row in rows),
        evidence_event_ids=tuple(row.event_id for row in rows),
    )


class BattleTimelineProjectionService:
    """Build one UI-independent projection for the combined timeline."""

    @staticmethod
    def infer_inputs(
        actions: Sequence[BattleInferredAction],
    ) -> tuple[BattleInferredInput, ...]:
        return tuple(_input_projection(action) for action in actions)

    @staticmethod
    def group_damage_hits(
        hits: Sequence[BattleAnalysisHit],
    ) -> tuple[BattleTimelineDamageGroup, ...]:
        active: dict[tuple[object, ...], list[BattleAnalysisHit]] = {}
        completed: list[list[BattleAnalysisHit]] = []
        for hit in sorted(
            hits,
            key=lambda item: (item.relative_time_us, item.sequence, item.is_follow_up),
        ):
            key = _group_key(hit)
            channel_key = str(key[3])
            gap_limit = _GROUP_GAP_US.get(channel_key, 1_200_000)
            if channel_key in {"direct", "direct_follow_up"}:
                interrupted = [
                    active_key
                    for active_key in active
                    if active_key != key
                    and active_key[:3] == key[:3]
                    and active_key[3] in {"direct", "direct_follow_up"}
                ]
                for active_key in interrupted:
                    completed.append(active.pop(active_key))
            current = active.get(key)
            if current and hit.relative_time_us - current[-1].relative_time_us > gap_limit:
                completed.append(current)
                current = None
            if current is None:
                current = []
                active[key] = current
            current.append(hit)
        completed.extend(active.values())
        groups = tuple(
            _build_group(ordinal, rows)
            for ordinal, rows in enumerate(
                sorted(
                    completed,
                    key=lambda items: (
                        items[0].relative_time_us,
                        items[0].sequence,
                        items[0].is_follow_up,
                    ),
                )
            )
        )
        return groups
