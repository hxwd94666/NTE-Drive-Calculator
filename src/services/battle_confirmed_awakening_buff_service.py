# 提供边界明确且已审计的觉醒 Buff 适配器。
"""Expose bounded, manually audited awakening Buff adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.services.battle_character_awakening_hit_service import (
    FADIA_GODSLAYER_REQUIREMENT,
    MITSUKI_ULTRA_REQUIREMENT,
    ZERO_FIRST_GAZE_REQUIREMENT,
)


@dataclass(frozen=True, slots=True)
class ConfirmedAwakeningBuffSpec:
    name: str
    scope: str
    event_type: str
    duration_seconds: float | None
    modifier_values: tuple[tuple[Any, ...], ...]
    stacking_type: str = "AggregateByTarget"


_SPECS = {
    "character_awaken:1046:Effect1": ConfirmedAwakeningBuffSpec(
        "初明凝视：铭隙鉴刻额外伤害（觉醒一）", "self",
        "STATIC_EQUIPPED_SOURCE", None,
        (("DefIgnore", 0.75, ZERO_FIRST_GAZE_REQUIREMENT),),
    ),
    "character_awaken:1051:Effect1": ConfirmedAwakeningBuffSpec(
        "初明凝视：铭隙鉴刻额外伤害（觉醒一）", "self",
        "STATIC_EQUIPPED_SOURCE", None,
        (("DefIgnore", 0.75, ZERO_FIRST_GAZE_REQUIREMENT),),
    ),
    "character_awaken:1036:Effect5": ConfirmedAwakeningBuffSpec(
        "花开见血（觉醒五）", "team", "STATIC_EQUIPPED_SOURCE", None,
        (("ToppleDamageUp", 3.00),),
    ),
    "character_awaken:1036:resonance_6": ConfirmedAwakeningBuffSpec(
        "鸩火灼心（六觉共鸣）", "self",
        "EBuffEventType::BUFF_EVENT_SKILL_AFTER_DAMAGE", 20.0,
        (("AtkUp", 0.40),),
    ),
    "character_awaken:1004:Effect2": ConfirmedAwakeningBuffSpec(
        "闹钟响彻四方（觉醒二）", "self",
        "EBuffEventType::BUFF_EVENT_QTE_BEGIN", 15.0,
        (("DamageUpGeneralBase", 0.15),),
    ),
    "character_awaken:1019:Effect3": ConfirmedAwakeningBuffSpec(
        "全勤奖金（觉醒三）", "self",
        (
            "PASSIVE_ANY_HIT|GE_ActorReaction_1_Damage,"
            "GE_ActorReaction_1_1019_Damage,覆纹,weave"
        ),
        15.0,
        (("AtkUp", 0.15),),
        "AggregateByTarget|RefreshWholeStack",
    ),
    "character_awaken:1019:Effect5": ConfirmedAwakeningBuffSpec(
        "第一直觉（觉醒五）", "self",
        (
            "PASSIVE_HIT|GE_Player_Mint_Skill1_Damage_New,"
            "GE_Player_Mint_Skill1_Damage_Test1"
        ),
        6.0,
        (("CritDamageBase", 0.25),),
    ),
    "character_awaken:1039:Effect3": ConfirmedAwakeningBuffSpec(
        "诅咒祝福之人（觉醒三）", "self", "STATIC_EQUIPPED_SOURCE", None,
        (("HPMaxUp", 0.30),),
    ),
    "character_awaken:1039:Effect5": ConfirmedAwakeningBuffSpec(
        "敌神者暴击提升（觉醒五）", "self",
        "EBuffEventType::BUFF_EVENT_Q_SKILL_BEGIN", 5.0,
        (("CritBase", 0.50, FADIA_GODSLAYER_REQUIREMENT),),
    ),
    "character_awaken:1039:resonance_6": ConfirmedAwakeningBuffSpec(
        "归一的圣洁之人（六觉共鸣）", "team",
        "STATIC_EQUIPPED_SOURCE", None, (("HPMaxUp", 0.10),),
    ),
    "character_awaken:1070:Effect5": ConfirmedAwakeningBuffSpec(
        "华彩乐章（觉醒五）", "self", "STATIC_EQUIPPED_SOURCE", None,
        (("CritBase", 0.15, MITSUKI_ULTRA_REQUIREMENT),),
    ),
}

_REPLACES_GENERIC = frozenset({
    "character_awaken:1003:Effect4",
    "character_awaken:1019:Effect3",
    "character_awaken:1036:Effect1",
    "character_awaken:1036:Effect5",
    "character_awaken:1039:resonance_6",
    "character_awaken:1070:Effect5",
    "character_awaken:1075:Effect5",
})


class BattleConfirmedAwakeningBuffService:
    @staticmethod
    def get(effect_definition_id: str) -> ConfirmedAwakeningBuffSpec | None:
        return _SPECS.get(effect_definition_id)

    @staticmethod
    def replaces_generic(effect_definition_id: str) -> bool:
        return effect_definition_id in _REPLACES_GENERIC


__all__ = ["BattleConfirmedAwakeningBuffService", "ConfirmedAwakeningBuffSpec"]
