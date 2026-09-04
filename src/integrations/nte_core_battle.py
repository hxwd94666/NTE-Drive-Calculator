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


def _optional_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    return _number(value, field)


def _identifier(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise NteCoreProtocolError(f"{field} must be a string or integer")
    normalized = str(value).strip()
    return normalized or None


def _decimal_text(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.isdecimal():
        raise NteCoreProtocolError(f"{field} must be a decimal string")
    return value


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
        max_hp_reduction=_number(
            item.get("max_hp_reduction"),
            "max_hp_reduction",
            default=0.0,
        ),
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


def parse_battle_record(value: Any) -> dict[str, Any]:
    """Validate the public battle_record_v1 envelope without inventing facts."""

    item = _object(value, "battle record")
    contract_version = _integer(item.get("contract_version"), "contract_version")
    if contract_version < 1:
        raise NteCoreProtocolError("contract_version must be positive")
    state = _text(item.get("state"), "state")
    if state not in {"live", "finalized"}:
        raise NteCoreProtocolError("state must be live or finalized")
    intervals = _array(item.get("time_stop_intervals", ()), "time_stop_intervals")
    for index, interval in enumerate(intervals):
        interval_item = _object(interval, f"time_stop_intervals[{index}]")
        field = f"time_stop_intervals[{index}].pause_type_mask"
        if "pause_type_mask" not in interval_item:
            if contract_version >= 5:
                raise NteCoreProtocolError(f"{field} is required for contract v5")
            continue
        pause_type_mask = interval_item.get("pause_type_mask")
        if pause_type_mask is None:
            continue
        if (
            isinstance(pause_type_mask, bool)
            or not isinstance(pause_type_mask, int)
            or not 0 < pause_type_mask <= 0xFFFF_FFFF
        ):
            raise NteCoreProtocolError(
                f"{field} must be null or a non-zero u32 integer"
            )
    summary = _object(item.get("summary"), "summary")
    abyss = _object(item.get("abyss", {}), "abyss")
    quality = _object(item.get("quality", {}), "quality")
    return {
        "contract_version": contract_version,
        "battle_record_id": _text(item.get("battle_record_id"), "battle_record_id"),
        "capture_operation_id": _optional_text(
            item.get("capture_operation_id"),
            "capture_operation_id",
        ),
        "team_snapshot_id": _optional_text(
            item.get("team_snapshot_id"),
            "team_snapshot_id",
        ),
        "generation": _decimal_text(item.get("generation"), "generation"),
        "state": state,
        "source": _text(item.get("source"), "source"),
        "started_at_unix": _optional_number(
            item.get("started_at_unix"),
            "started_at_unix",
        ),
        "ended_at_unix": _optional_number(
            item.get("ended_at_unix"),
            "ended_at_unix",
        ),
        "finalized_at_unix_ms": _optional_integer(
            item.get("finalized_at_unix_ms"),
            "finalized_at_unix_ms",
        ),
        "axis_complete": _boolean(
            item.get("axis_complete"),
            "axis_complete",
            default=True,
        ),
        "axis_first_sequence": _decimal_text(
            item.get("axis_first_sequence"),
            "axis_first_sequence",
            optional=True,
        ),
        "axis_total_hits": _decimal_text(
            item.get("axis_total_hits", "0"),
            "axis_total_hits",
        ),
        "time_stop_intervals": [dict(interval) for interval in intervals],
        "abyss": dict(abyss),
        "summary": dict(summary),
        "quality": dict(quality),
    }


def _axis_hit(value: Any, field: str, *, contract_version: int) -> dict[str, Any]:
    item = _object(value, field)
    raw_character_id = _optional_integer(
        item.get("character_id", item.get("char_id")),
        f"{field}.character_id",
    )
    character_known = _boolean(
        item.get("character_known", item.get("char_known")),
        f"{field}.character_known",
        default=raw_character_id is not None and raw_character_id > 0,
    )
    if character_known and not raw_character_id:
        raise NteCoreProtocolError(
            f"{field}.character_id must be positive when character_known is true"
        )
    character_id = (
        raw_character_id
        if character_known and raw_character_id is not None and raw_character_id > 0
        else None
    )
    character_name = item.get("character_name", item.get("char_name"))
    damage = _number(item.get("damage"), f"{field}.damage", default=0.0)
    overkill_damage = (
        _number(item.get("overkill_damage"), f"{field}.overkill_damage")
        if contract_version >= 3
        else _optional_number(
            item.get("overkill_damage"),
            f"{field}.overkill_damage",
        )
    )
    if overkill_damage is not None and overkill_damage > damage:
        raise NteCoreProtocolError(
            f"{field}.overkill_damage cannot exceed primary damage"
        )
    max_hp_reduction = (
        _number(
            item.get("max_hp_reduction"),
            f"{field}.max_hp_reduction",
        )
        if contract_version >= 4
        else _optional_number(
            item.get("max_hp_reduction"),
            f"{field}.max_hp_reduction",
        )
    )
    if max_hp_reduction is not None and max_hp_reduction < 0:
        raise NteCoreProtocolError(
            f"{field}.max_hp_reduction cannot be negative"
        )
    raw_labels = item.get("follow_up_labels")
    if raw_labels is None:
        raw_labels = [
            value
            for key in (
                "follow_up_damage_name",
                "follow_up_damage_component",
                "follow_up_attack_type",
                "follow_up_damage_attribute",
            )
            if (value := item.get(key)) is not None
        ]
    labels = _array(raw_labels, f"{field}.follow_up_labels")
    normalized_labels = [
        _text(label, f"{field}.follow_up_labels[{index}]")
        for index, label in enumerate(labels)
    ]
    raw_target_context = item.get("target_context")
    if raw_target_context is None:
        target_context: list[str] = []
    elif isinstance(raw_target_context, str):
        target_context = [raw_target_context] if raw_target_context.strip() else []
    else:
        target_context = [
            _text(label, f"{field}.target_context[{index}]")
            for index, label in enumerate(
                _array(raw_target_context, f"{field}.target_context")
            )
        ]
    return {
        "battle_record_id": _text(
            item.get("battle_record_id"),
            f"{field}.battle_record_id",
        ),
        "sequence": _decimal_text(item.get("sequence"), f"{field}.sequence"),
        "timestamp_unix": _number(
            item.get("timestamp_unix"),
            f"{field}.timestamp_unix",
        ),
        "relative_time_seconds": _number(
            item.get("relative_time_seconds"),
            f"{field}.relative_time_seconds",
        ),
        "abyss_half": _optional_text(item.get("abyss_half"), f"{field}.abyss_half"),
        "character_id": character_id,
        "character_name": _optional_text(character_name, f"{field}.character_name"),
        "character_known": character_known,
        "character_source": _optional_text(
            item.get("character_source"),
            f"{field}.character_source",
        ),
        "attribution_status": _optional_text(
            item.get("attribution_status"),
            f"{field}.attribution_status",
        ),
        "attribution_source": _optional_text(
            item.get("attribution_source"),
            f"{field}.attribution_source",
        ),
        "attribution_unknown_reason": _optional_text(
            item.get("attribution_unknown_reason"),
            f"{field}.attribution_unknown_reason",
        ),
        "team_snapshot_id": _optional_text(
            item.get("team_snapshot_id"),
            f"{field}.team_snapshot_id",
        ),
        "direction": _text(item.get("direction"), f"{field}.direction"),
        "damage": damage,
        "overkill_damage": overkill_damage,
        "max_hp_reduction": max_hp_reduction,
        "follow_up_damage": _number(
            item.get("follow_up_damage"),
            f"{field}.follow_up_damage",
            default=0.0,
        ),
        "total_damage": _number(
            item.get("total_damage"),
            f"{field}.total_damage",
            default=0.0,
        ),
        "follow_up_timestamp_unix": _optional_number(
            item.get("follow_up_timestamp_unix"),
            f"{field}.follow_up_timestamp_unix",
        ),
        "target_id": _identifier(item.get("target_id"), f"{field}.target_id"),
        "target_name": _optional_text(item.get("target_name"), f"{field}.target_name"),
        "target_name_en": _optional_text(
            item.get("target_name_en"),
            f"{field}.target_name_en",
        ),
        "target_name_ja": _optional_text(
            item.get("target_name_ja"),
            f"{field}.target_name_ja",
        ),
        "target_monster_id": _identifier(
            item.get("target_monster_id"),
            f"{field}.target_monster_id",
        ),
        "target_context": target_context,
        "target_hp_before": _optional_number(
            item.get("target_hp_before"),
            f"{field}.target_hp_before",
        ),
        "target_hp_after": _optional_number(
            item.get("target_hp_after"),
            f"{field}.target_hp_after",
        ),
        "target_max_hp": _optional_number(
            item.get("target_max_hp"),
            f"{field}.target_max_hp",
        ),
        "target_hp_percent": _optional_number(
            item.get("target_hp_percent"),
            f"{field}.target_hp_percent",
        ),
        "gameplay_effect_index": _optional_integer(
            item.get("gameplay_effect_index"),
            f"{field}.gameplay_effect_index",
        ),
        "gameplay_effect_name": _optional_text(
            item.get("gameplay_effect_name"),
            f"{field}.gameplay_effect_name",
        ),
        "ability_name": _optional_text(
            item.get("ability_name"),
            f"{field}.ability_name",
        ),
        "damage_name": _optional_text(item.get("damage_name"), f"{field}.damage_name"),
        "damage_component": _optional_text(
            item.get("damage_component"),
            f"{field}.damage_component",
        ),
        "attack_type": _optional_text(item.get("attack_type"), f"{field}.attack_type"),
        "damage_attribute": _optional_text(
            item.get("damage_attribute"),
            f"{field}.damage_attribute",
        ),
        "follow_up_damage_name": _optional_text(
            item.get("follow_up_damage_name"),
            f"{field}.follow_up_damage_name",
        ),
        "follow_up_damage_component": _optional_text(
            item.get("follow_up_damage_component"),
            f"{field}.follow_up_damage_component",
        ),
        "follow_up_attack_type": _optional_text(
            item.get("follow_up_attack_type"),
            f"{field}.follow_up_attack_type",
        ),
        "follow_up_damage_attribute": _optional_text(
            item.get("follow_up_damage_attribute"),
            f"{field}.follow_up_damage_attribute",
        ),
        "follow_up_labels": normalized_labels,
    }


def parse_battle_axis(value: Any) -> dict[str, Any]:
    """Validate one versioned battle-axis page and normalize its hit fields."""

    item = _object(value, "battle axis")
    contract_version = _integer(item.get("contract_version"), "contract_version")
    if contract_version < 1:
        raise NteCoreProtocolError("contract_version must be positive")
    rows = _array(item.get("rows", ()), "rows")
    return {
        "contract_version": contract_version,
        "battle_record_id": _text(item.get("battle_record_id"), "battle_record_id"),
        "generation": _decimal_text(item.get("generation"), "generation"),
        "finalized": _boolean(item.get("finalized"), "finalized", default=False),
        "complete": _boolean(item.get("complete"), "complete", default=True),
        "first_available_cursor": _decimal_text(
            item.get("first_available_cursor"),
            "first_available_cursor",
            optional=True,
        ),
        "cursor": _decimal_text(item.get("cursor"), "cursor", optional=True),
        "next_cursor": _decimal_text(
            item.get("next_cursor"),
            "next_cursor",
            optional=True,
        ),
        "total_hits": _decimal_text(item.get("total_hits", "0"), "total_hits"),
        "retained_hits": _integer(
            item.get("retained_hits"),
            "retained_hits",
            default=len(rows),
        ),
        "rows": [
            _axis_hit(row, f"rows[{index}]", contract_version=contract_version)
            for index, row in enumerate(rows)
        ],
    }
