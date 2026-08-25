# Buff 区间终点、角色切换与逐击激活的纯规则支持。
"""Reusable interval operations for static battle Buff inference."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredAction,
    BattleInferredBuffInterval,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)
from src.services.battle_damage_composition_service import (
    classify_battle_hit_channel,
    classify_battle_hit_reaction_trigger,
)
from src.services.battle_trigger_requirement_service import (
    trigger_requirement_applies_to_action,
    trigger_requirement_applies_to_hit,
)


@dataclass(frozen=True, slots=True)
class BuffOccurrence:
    time_us: int
    state_confidence: str
    action_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()
    target_id: str = ""


class BattleBuffIntervalSupportMixin:
    """State-free helpers shared by the Buff inference orchestration service."""

    @staticmethod
    def _interval_end(
        rule: Any,
        start_us: int,
        removals: Sequence[BuffOccurrence],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
    ) -> int | None:
        next_remove = next(
            (row.time_us for row in removals if row.time_us > start_us),
            None,
        )
        if rule.event_type == "STATIC_EQUIPPED_SOURCE":
            return battle_end_us
        policy = rule.duration_policy.casefold()
        if "instant" in policy:
            return None
        if rule.duration_seconds is not None:
            start_active_us = project_timeline_time_us(
                start_us,
                battle_start_us=0,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
            )
            duration_end = unproject_timeline_time_us(
                start_active_us + round(rule.duration_seconds * 1_000_000),
                battle_start_us=0,
                battle_end_us=battle_end_us,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
                prefer_interval_end=True,
            )
            return min(duration_end, next_remove or battle_end_us)
        if "infinite" in policy:
            return next_remove or battle_end_us
        if next_remove is not None:
            return next_remove
        if "begin" in rule.event_type.casefold() and start_us == 0:
            return battle_end_us
        return None

    @staticmethod
    def _action_occurrence(
        action: BattleInferredAction,
        *,
        at_end: bool,
    ) -> BuffOccurrence:
        return BuffOccurrence(
            action.end_us if at_end else action.start_us,
            "低",
            action_ids=(action.action_id,),
            event_ids=action.evidence_event_ids,
        )

    @classmethod
    def _occurrence_ends(
        cls,
        rule: Any,
        occurrences: Sequence[BuffOccurrence],
        removals: Sequence[BuffOccurrence],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[tuple[BuffOccurrence, int], ...]:
        ordered = tuple(sorted(occurrences, key=lambda row: row.time_us))
        cooldown_seconds = getattr(rule, "cooldown_seconds", None)
        if cooldown_seconds is not None and cooldown_seconds > 0:
            accepted = []
            last_active_us: int | None = None
            cooldown_us = round(cooldown_seconds * 1_000_000)
            for occurrence in ordered:
                active_us = project_timeline_time_us(
                    occurrence.time_us,
                    battle_start_us=0,
                    intervals=time_stop_intervals,
                    mode=ACTIVE_TIME_MODE,
                )
                if last_active_us is not None and active_us - last_active_us < cooldown_us:
                    continue
                accepted.append(occurrence)
                last_active_us = active_us
            ordered = tuple(accepted)
        if "refreshwholestack" not in rule.stacking_type.casefold():
            result = []
            for occurrence in ordered:
                end_us = cls._interval_end(
                    rule,
                    occurrence.time_us,
                    removals,
                    battle_end_us,
                    time_stop_intervals,
                )
                if end_us is not None and end_us > occurrence.time_us:
                    result.append((occurrence, end_us))
            return tuple(result)
        chains: list[list[BuffOccurrence]] = []
        chain_ends: list[int] = []
        for occurrence in ordered:
            end_us = cls._interval_end(
                rule,
                occurrence.time_us,
                removals,
                battle_end_us,
                time_stop_intervals,
            )
            if end_us is None or end_us <= occurrence.time_us:
                continue
            if chains and occurrence.time_us < chain_ends[-1]:
                chains[-1].append(occurrence)
                chain_ends[-1] = end_us
            else:
                chains.append([occurrence])
                chain_ends.append(end_us)
        return tuple(
            (occurrence, chain_end)
            for chain, chain_end in zip(chains, chain_ends, strict=True)
            for occurrence in chain
        )

    @staticmethod
    def _role_change_occurrences(
        character_id: int,
        actions: Sequence[BattleInferredAction],
        *,
        entering: bool,
    ) -> tuple[BuffOccurrence, ...]:
        ordered = sorted(actions, key=lambda row: (row.start_us, row.action_id))
        previous: int | None = None
        result = []
        for action in ordered:
            current = action.character_id
            if current == previous:
                continue
            matched = current == character_id if entering else previous == character_id
            if matched:
                result.append(BuffOccurrence(
                    action.start_us,
                    "低",
                    action_ids=(action.action_id,),
                    event_ids=action.evidence_event_ids,
                ))
            previous = current
        return tuple(result)

    @classmethod
    def _occurrences(
        cls,
        rule: Any,
        *,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
    ) -> tuple[BuffOccurrence, ...]:
        event = rule.event_type.casefold()
        role_actions = tuple(
            row
            for row in actions
            if row.character_id == rule.source_character_id
            and trigger_requirement_applies_to_action(
                getattr(rule, "application_requirement_asset_path", ""),
                row,
            )
        )
        role_hits = tuple(
            row for row in hits
            if row.character_id == rule.source_character_id
            and row.direction == "outgoing"
            and not row.is_follow_up
            and trigger_requirement_applies_to_hit(
                getattr(rule, "application_requirement_asset_path", ""),
                row,
            )
        )
        if event == "static_equipped_source":
            return (BuffOccurrence(0, "低"),)
        if event == "passive_static":
            return (BuffOccurrence(0, "高"),)
        if event.startswith("suit_team_attribute_hit|"):
            attribute = event.split("|", 1)[1]
            return tuple(
                BuffOccurrence(
                    min(battle_end_us, row.relative_time_us + 1),
                    "中" if row.damage_attribute else "低",
                    event_ids=(row.event_id,),
                    target_id=row.target_id,
                )
                for row in hits
                if row.direction == "outgoing"
                and row.damage_attribute.casefold() == attribute
            )
        if event == "suit_source_attack_hit|a":
            return tuple(
                BuffOccurrence(
                    min(battle_end_us, row.relative_time_us + 1),
                    "中" if row.ability_id else "低",
                    event_ids=(row.event_id,),
                    target_id=row.target_id,
                )
                for row in role_hits
                if (
                    row.attack_type in {"普攻", "普通攻击"}
                    or "melee" in row.ability_id.casefold()
                )
            )
        if event.startswith((
            "suit_source_reaction_after|",
            "suit_team_reaction_after|",
            "suit_target_state_backfill|",
            "suit_target_state_forward|",
        )):
            prefix, raw_channels = event.split("|", 1)
            channel_offsets = {}
            for token in raw_channels.split(","):
                channel, separator, raw_offset = token.partition(":")
                if channel:
                    channel_offsets[channel] = (
                        float(raw_offset) if separator and raw_offset else 0.0
                    )
            candidate_channels = tuple(
                (
                    row,
                    (classify_battle_hit_reaction_trigger(row) or ("", ""))[0],
                )
                for row in hits
                if row.direction == "outgoing"
                and (
                    prefix != "suit_source_reaction_after"
                    or row.character_id == rule.source_character_id
                )
            )
            results = []
            for row, channel in candidate_channels:
                if channel not in channel_offsets:
                    continue
                offset_us = round(channel_offsets[channel] * 1_000_000)
                if prefix in {
                    "suit_source_reaction_after",
                    "suit_team_reaction_after",
                    "suit_target_state_backfill",
                } and offset_us > 0:
                    hit_active_us = project_timeline_time_us(
                        row.relative_time_us,
                        battle_start_us=0,
                        intervals=time_stop_intervals,
                        mode=ACTIVE_TIME_MODE,
                    )
                    time_us = unproject_timeline_time_us(
                        max(0, hit_active_us - offset_us),
                        battle_start_us=0,
                        battle_end_us=battle_end_us,
                        intervals=time_stop_intervals,
                        mode=ACTIVE_TIME_MODE,
                        prefer_interval_end=False,
                    )
                else:
                    time_us = min(battle_end_us, row.relative_time_us + 1)
                results.append(BuffOccurrence(
                    time_us,
                    "低",
                    event_ids=(row.event_id,),
                    target_id=row.target_id,
                ))
            return tuple(results)
        if event.startswith("passive_hit|") or event.startswith("passive_any_hit|"):
            tokens = tuple(
                token.casefold()
                for token in rule.event_type.split("|", 1)[1].split(",")
                if token
            )
            candidates = (
                tuple(row for row in hits if row.direction == "outgoing")
                if event.startswith("passive_any_hit|")
                else role_hits
            )
            return tuple(
                BuffOccurrence(
                    row.relative_time_us + 1,
                    "中" if row.gameplay_effect_id else "低",
                    event_ids=(row.event_id,),
                    target_id=row.target_id,
                )
                for row in candidates
                if any(
                    token in "|".join((
                        row.gameplay_effect_id,
                        row.ability_id,
                        row.skill_name,
                        row.damage_name,
                    )).casefold()
                    for token in tokens
                )
            )
        if event.startswith((
            "ability_event|", "ability_event_end|",
            "ability_event_after_end|", "ability_event_offset|",
        )):
            input_kind = rule.event_type.split("|", 3)[1]
            matched = (
                (row for row in role_actions if "闪避" in row.action_name)
                if input_kind == "PERFECT_EVADE"
                else (row for row in role_actions if row.input_kind == input_kind)
            )
            results = []
            for row in matched:
                if event.startswith("ability_event_after_end|"):
                    time_us = min(battle_end_us, row.end_us + 1)
                elif event.startswith("ability_event_offset|"):
                    offset = float(rule.event_type.split("|", 3)[2])
                    time_us = min(battle_end_us, row.start_us + round(offset * 1_000_000))
                else:
                    time_us = row.end_us if event.startswith("ability_event_end|") else row.start_us
                results.append(BuffOccurrence(
                    time_us,
                    "中" if "after_end" in event or "offset" in event else "低",
                    action_ids=(row.action_id,),
                    event_ids=row.evidence_event_ids,
                ))
            return tuple(results)
        if event.endswith("buff_enter_battle") or event.endswith("event_begin"):
            return (BuffOccurrence(0, "中"),)
        if event.endswith("buff_leave_battle") or event.endswith("event_finish"):
            return (BuffOccurrence(battle_end_us, "中"),)
        action_kind = (
            "Q" if "q_skill_begin" in event
            else "E" if "e_skill_begin" in event
            else "QTE" if "qte_begin" in event
            else None
        )
        if action_kind is not None:
            return tuple(
                cls._action_occurrence(row, at_end=False)
                for row in role_actions if row.input_kind == action_kind
            )
        if any(token in event for token in (
            "skill_realfinish", "skill_finish", "montage_skill_finish",
        )):
            return tuple(cls._action_occurrence(row, at_end=True) for row in role_actions)
        if event.endswith("skill_begin"):
            return tuple(cls._action_occurrence(row, at_end=False) for row in role_actions)
        if "skill_hit_before_calc" in event:
            return tuple(
                BuffOccurrence(
                    row.relative_time_us,
                    "低",
                    event_ids=(row.event_id,),
                    target_id=row.target_id,
                )
                for row in role_hits
            )
        if any(token in event for token in (
            "skill_after_damage", "skill_after_hit",
        )):
            return tuple(
                BuffOccurrence(
                    min(battle_end_us, row.relative_time_us + 1),
                    "低",
                    event_ids=(row.event_id,),
                    target_id=row.target_id,
                )
                for row in role_hits
            )
        if "perfect_evade" in event:
            return tuple(
                cls._action_occurrence(row, at_end=False)
                for row in role_actions
                if "闪避" in row.action_name
                or any("evade" in effect.casefold() for effect in row.gameplay_effect_ids)
            )
        if "parry_attack" in event:
            return tuple(
                cls._action_occurrence(row, at_end=False)
                for row in role_actions
                if "格挡" in row.action_name
                or any("parry" in effect.casefold() for effect in row.gameplay_effect_ids)
            )
        if "change_role" in event:
            return cls._role_change_occurrences(
                rule.source_character_id,
                actions,
                entering="int" in event or "in_begin" in event,
            )
        return ()

    @staticmethod
    def _basis(rule: Any, occurrence: BuffOccurrence) -> str:
        if rule.source_kind == "confirmed_character_passive":
            return (
                f"本场冻结角色突破已解锁 {rule.source_effect_definition_id}；"
                f"按已审计角色被动事件 {rule.event_type} 投影。"
                "运行时 Buff 事件可用时仍由高置信证据覆盖。"
            )
        if rule.event_type == "STATIC_EQUIPPED_SOURCE":
            return (
                "冻结配装启用了该运行时效果来源；战报冻结面板是战前基础值，"
                "不含进场后弧盘、套装或觉醒 Buff，因此按整场低置信区间投影。"
            )
        return (
            f"冻结配装效果 {rule.source_effect_definition_id} 的静态事件 "
            f"{rule.event_type} 与推算动作/逐击对齐；正式触发条件 "
            f"{getattr(rule, 'application_requirement_asset_path', '') or '无'}；"
            "区间状态置信度"
            f" {occurrence.state_confidence}，不是 nte-core 实测 Buff。"
        )

    @staticmethod
    def active_for_hit(
        intervals: Sequence[BattleInferredBuffInterval],
        hit: BattleAnalysisHit,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        return tuple(
            row
            for row in intervals
            if row.start_us <= hit.relative_time_us < row.end_us
            and (
                row.target_scope == "team"
                or (
                    row.target_scope == "team_others"
                    and row.source_character_id != hit.character_id
                )
                or (
                    row.target_scope == "self"
                    and row.source_character_id == hit.character_id
                )
                or row.target_scope == f"character:{hit.character_id}"
                or row.target_scope in {"target", "unknown"}
            )
        )
