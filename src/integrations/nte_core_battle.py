# 校验 nte-core 战斗 DTO 并投影为领域值。
"""Validate and project nte-core battle DTOs into domain values."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.battle_report import (
    BattleAbyssHalfSummary,
    BattleAbyssSummary,
    BattleCharacterSummary,
    BattleQualitySummary,
    BattleSkillSummary,
    BattleSummary,
)
from src.integrations.nte_core import NteCoreProtocolError


def _object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NteCoreProtocolError(f"{field} must be an object")
    return value


def _array(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise NteCoreProtocolError(f"{field} must be an array")
    return value


def _text(value: Any, field: str, *, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str):
        raise NteCoreProtocolError(f"{field} must be a string")
    return value


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _optional_integer(value: Any, field: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field)


def _number(value: Any, field: str, *, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NteCoreProtocolError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise NteCoreProtocolError(f"{field} must be finite and non-negative")
    return result


def _integer(value: Any, field: str, *, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NteCoreProtocolError(f"{field} must be a non-negative integer")
    return value


def _boolean(value: Any, field: str, *, default: bool | None = None) -> bool:
    if value is None and default is not None:
        return default
    if not isinstance(value, bool):
        raise NteCoreProtocolError(f"{field} must be a boolean")
    return value


def _character(value: Any, field: str) -> BattleCharacterSummary:
    item = _object(value, field)
    return BattleCharacterSummary(
        character_id=_integer(item.get("char_id"), f"{field}.char_id"),
        name=_text(item.get("name"), f"{field}.name"),
        hits=_integer(item.get("hits"), f"{field}.hits", default=0),
        damage=_number(item.get("damage"), f"{field}.damage", default=0.0),
        dps=_number(item.get("dps"), f"{field}.dps", default=0.0),
        damage_share_percent=_number(
            item.get("damage_share_percent"),
            f"{field}.damage_share_percent",
            default=0.0,
        ),
        hits_taken=_integer(item.get("hits_taken"), f"{field}.hits_taken", default=0),
        damage_taken=_number(
            item.get("damage_taken"), f"{field}.damage_taken", default=0.0
        ),
    )


def _skill(value: Any, field: str) -> BattleSkillSummary:
    item = _object(value, field)
    return BattleSkillSummary(
        character_id=_integer(item.get("char_id"), f"{field}.char_id"),
        character_name=_text(item.get("char_name"), f"{field}.char_name"),
        name=_text(item.get("name"), f"{field}.name"),
        category=_text(item.get("category"), f"{field}.category", default="unknown"),
        hits=_integer(item.get("hits"), f"{field}.hits", default=0),
        damage=_number(item.get("damage"), f"{field}.damage", default=0.0),
        damage_share_percent=_number(
            item.get("damage_share_percent"),
            f"{field}.damage_share_percent",
            default=0.0,
        ),
        ability_name=_optional_text(item.get("ability_name"), f"{field}.ability_name"),
        gameplay_effect_name=_optional_text(
            item.get("gameplay_effect_name"), f"{field}.gameplay_effect_name"
        ),
        is_follow_up=_boolean(
            item.get("is_follow_up"), f"{field}.is_follow_up", default=False
        ),
    )


def _characters(value: Any, field: str) -> tuple[BattleCharacterSummary, ...]:
    return tuple(
        _character(item, f"{field}[{index}]")
        for index, item in enumerate(_array(value, field))
    )


def _skills(value: Any, field: str) -> tuple[BattleSkillSummary, ...]:
    return tuple(
        _skill(item, f"{field}[{index}]")
        for index, item in enumerate(_array(value, field))
    )


def _abyss_half(value: Any, field: str) -> BattleAbyssHalfSummary | None:
    if value is None:
        return None
    item = _object(value, field)
    return BattleAbyssHalfSummary(
        half=_text(item.get("half"), f"{field}.half"),
        duration_seconds=_number(
            item.get("duration_seconds"), f"{field}.duration_seconds", default=0.0
        ),
        total_damage=_number(
            item.get("total_damage"), f"{field}.total_damage", default=0.0
        ),
        total_dps=_number(item.get("total_dps"), f"{field}.total_dps", default=0.0),
        characters=_characters(item.get("characters", ()), f"{field}.characters"),
        skills=_skills(item.get("skills", ()), f"{field}.skills"),
    )


def _abyss(value: Any) -> BattleAbyssSummary:
    if value is None:
        return BattleAbyssSummary()
    item = _object(value, "abyss")
    return BattleAbyssSummary(
        detected=_boolean(item.get("detected"), "abyss.detected", default=False),
        floor=_optional_integer(item.get("floor"), "abyss.floor"),
        active_half=_optional_text(item.get("active_half"), "abyss.active_half"),
        success=_boolean(item.get("success"), "abyss.success", default=False),
        first_half=_abyss_half(item.get("first_half"), "abyss.first_half"),
        second_half=_abyss_half(item.get("second_half"), "abyss.second_half"),
    )


def _quality(value: Any) -> BattleQualitySummary:
    if value is None:
        return BattleQualitySummary()
    item = _object(value, "quality")
    return BattleQualitySummary(
        source=_text(item.get("source"), "quality.source", default="unknown"),
        packet_count=_integer(item.get("packet_count"), "quality.packet_count", default=0),
        packets_with_hits=_integer(
            item.get("packets_with_hits"), "quality.packets_with_hits", default=0
        ),
        hit_count=_integer(item.get("hit_count"), "quality.hit_count", default=0),
        outgoing_hits=_integer(
            item.get("outgoing_hits"), "quality.outgoing_hits", default=0
        ),
        incoming_hits=_integer(
            item.get("incoming_hits"), "quality.incoming_hits", default=0
        ),
        unknown_direction_hits=_integer(
            item.get("unknown_direction_hits"),
            "quality.unknown_direction_hits",
            default=0,
        ),
        unknown_character_count=_integer(
            item.get("unknown_character_count"),
            "quality.unknown_character_count",
            default=0,
        ),
        unknown_character_hits=_integer(
            item.get("unknown_character_hits"),
            "quality.unknown_character_hits",
            default=0,
        ),
        unmapped_skill_rows=_integer(
            item.get("unmapped_skill_rows"),
            "quality.unmapped_skill_rows",
            default=0,
        ),
        unmapped_skill_hits=_integer(
            item.get("unmapped_skill_hits"),
            "quality.unmapped_skill_hits",
            default=0,
        ),
        unmapped_gameplay_effect_count=_integer(
            item.get("unmapped_gameplay_effect_count"),
            "quality.unmapped_gameplay_effect_count",
            default=0,
        ),
    )


def parse_battle_summary(value: Any, *, sequence: int = 0) -> BattleSummary:
    item = _object(value, "battle summary")
    return BattleSummary(
        duration_seconds=_number(
            item.get("duration_seconds"), "duration_seconds", default=0.0
        ),
        dps_time_mode=_text(
            item.get("dps_time_mode"), "dps_time_mode", default="subtract_time_stop"
        ),
        total_damage=_number(item.get("total_damage"), "total_damage", default=0.0),
        total_dps=_number(item.get("total_dps"), "total_dps", default=0.0),
        total_damage_taken=_number(
            item.get("total_damage_taken"), "total_damage_taken", default=0.0
        ),
        total_hits=_integer(item.get("total_hits"), "total_hits", default=0),
        characters=_characters(item.get("characters", ()), "characters"),
        skills=_skills(item.get("skills", ()), "skills"),
        abyss=_abyss(item.get("abyss")),
        quality=_quality(item.get("quality")),
        sequence=sequence,
    )


def parse_battle_summary_event(event: Any) -> BattleSummary:
    message = _object(event, "battle event")
    if message.get("method") != "event.battle.summary":
        raise NteCoreProtocolError("battle event has an unexpected method")
    params = _object(message.get("params"), "battle event.params")
    sequence = _integer(params.get("sequence"), "battle event.params.sequence", default=0)
    return parse_battle_summary(params, sequence=sequence)
