# 已审计觉醒目录与逐击门禁共用显式选择判定，不把条件属性提升为常驻面板。
from __future__ import annotations
from typing import Any, Mapping
from src.domain.battle_report.timeline.events import BattleAnalysisHit
from dataclasses import dataclass


ZERO_FIRST_GAZE_REQUIREMENT = "battle-awakening:zero-first-gaze-extra-hit"


FADIA_GODSLAYER_REQUIREMENT = "battle-awakening:fadia-godslayer"


MITSUKI_ULTRA_REQUIREMENT = "battle-awakening:mitsuki-ultra"


LINKO_COATTACK_REQUIREMENT = "battle-awakening:linko-coattack"


SHINKU_RAGE_REQUIREMENT = "battle-character:shinku-rage-hit"


SHINKU_RAGE_DAMAGE_IDS = frozenset(
    f"ge_player_shinku_{name}_rage_damage"
    for name in (
        "airattack", "branch1", "branch2", "branch3", "branch4", "branch5",
        "melee1_1", "melee1", "melee2", "melee3_1", "melee3", "melee4_1",
        "melee4", "melee5", "perfectevadeattack", "skill1", "skill2",
        "ultraskill2", "ultraskillpre", "ultraskill",
    )
)


_SHINKU_RAGE_SKILL_COEF_IDS = frozenset(
    f"ge_player_shinku_{name}_rage_damage"
    for name in ("skill1", "skill2", "ultraskillpre", "ultraskill", "ultraskill2")
)


_ZERO_FIRST_GAZE_DAMAGE_IDS = frozenset({
    "ge_player_female046_skill_kill_damage_lv1",
    "ge_player_female046_skill_kill_damage_lv2",
    "ge_player_female051_skill_kill_damage_lv1",
    "ge_player_female051_skill_kill_damage_lv2",
})


_ZERO_CHARACTER_IDS = frozenset({1046, 1051})


_KUHARA_ATTACHMENT_DAMAGE_ID = "ge_player_kuhara_seed_damage"


_LINKO_ULTRA_BASE_DAMAGE_ID = "GE_Player_Radio072_UltraSkill3_Damage"


_LINKO_ULTRA_EFFECT_TWO_DAMAGE_ID = (
    "GE_Player_Radio072_UltraSkill3_Damage_level2"
)


# 显式选择优先；旧数值等级只按原 contract 回退，格式异常仍视为未解锁。
def awakening_enabled(character: Mapping[str, Any], effect_id: str) -> bool:
    profile = character.get("profile")
    profile = profile if isinstance(profile, Mapping) else {}
    if bool(profile.get("awakening_selection_initialized")):
        return effect_id in {
            str(value) for value in profile.get("selected_awaken_effect_ids") or ()
        }
    try:
        required = int(effect_id.removeprefix("Effect"))
        current = int(
            profile.get("awakening_level")
            or character.get("awakening_level")
            or 0
        )
    except (TypeError, ValueError):
        return False
    return current >= required


def character_awakening_damage_multiplier(
    character: Mapping[str, Any],
    *,
    damage_id: str,
    shinku_rage_skill_coefficient: float | None = None,
) -> tuple[float | None, str]:
    """Return explicit per-hit awakening multipliers backed by formal effects."""

    if (
        int(character.get("character_id") or 0) == 1076
        and damage_id.casefold() in _SHINKU_RAGE_SKILL_COEF_IDS
        and awakening_enabled(character, "Effect6")
    ):
        if shinku_rage_skill_coefficient is None:
            return None, "觉醒六强化 E/Q 缺少正式 Shinku_RageSkillDmgCoefL6 曲线"
        return 1.0 + shinku_rage_skill_coefficient, (
            "觉醒六「赤龙的藏宝地」：正式强化 E/Q CoefModify "
            f"+{shinku_rage_skill_coefficient:.0%}"
        )
    if (
        int(character.get("character_id") or 0) == 1055
        and damage_id.casefold() == _KUHARA_ATTACHMENT_DAMAGE_ID
        and awakening_enabled(character, "Effect2")
    ):
        return 2.0, "觉醒二「过往消息皆为利刃」：致命玫约伤害额外提升 100%"
    return 1.0, ""


def character_awakening_damage_id(
    character: Mapping[str, Any] | None,
    *,
    damage_id: str,
) -> str:
    """Select the formal GE variant changed by an explicit awakening edit."""

    if character is None or int(character.get("character_id") or 0) != 1072:
        return damage_id
    candidates = {
        _LINKO_ULTRA_BASE_DAMAGE_ID.casefold(),
        _LINKO_ULTRA_EFFECT_TWO_DAMAGE_ID.casefold(),
    }
    if damage_id.casefold() not in candidates:
        return damage_id
    return (
        _LINKO_ULTRA_EFFECT_TWO_DAMAGE_ID
        if awakening_enabled(character, "Effect2")
        else _LINKO_ULTRA_BASE_DAMAGE_ID
    )


def character_awakening_requirement_applies(
    requirement: str,
    hit: BattleAnalysisHit,
) -> tuple[bool, str]:
    """Return whether one confirmed awakening modifier belongs to this hit."""

    normalized = str(requirement or "").casefold()
    if normalized == SHINKU_RAGE_REQUIREMENT:
        applies = (
            hit.character_id == 1076
            and hit.direction == "outgoing"
            and hit.gameplay_effect_id.casefold() in SHINKU_RAGE_DAMAGE_IDS
        )
        return applies, "" if applies else "升腾伤害增益仅采用本击正式 Rage 技能证据"
    identity = "|".join((
        hit.attack_type,
        hit.ability_id,
        hit.gameplay_effect_id,
        hit.skill_name,
        hit.damage_name,
    )).casefold()
    if normalized == ZERO_FIRST_GAZE_REQUIREMENT:
        applies = (
            hit.character_id in _ZERO_CHARACTER_IDS
            and hit.gameplay_effect_id.casefold() in _ZERO_FIRST_GAZE_DAMAGE_IDS
        )
        return applies, (
            "" if applies else "初明凝视的 75% 防御穿透只作用于铭隙鉴刻的额外伤害"
        )
    if normalized == FADIA_GODSLAYER_REQUIREMENT:
        applies = hit.character_id == 1039 and "fadia_ultraskillmelee" in identity
        return applies, "" if applies else "觉醒五暴击提升只作用于敌神者"
    if normalized == MITSUKI_ULTRA_REQUIREMENT:
        applies = hit.character_id == 1070 and "ultraskill" in identity
        return applies, "" if applies else "觉醒五暴击提升只作用于 Q 技能伤害"
    if normalized == LINKO_COATTACK_REQUIREMENT:
        applies = hit.formula_context_kind.startswith("linko_coattack:")
        return applies, "" if applies else "灵可觉醒六暴击率只作用于同频合击"
    if "con_mint_lv6" in normalized:
        if hit.target_hp_before is None or not hit.target_max_hp:
            return False, "缺少命中前目标生命，无法确认目标低于 40%"
        applies = hit.target_hp_before / hit.target_max_hp < 0.40
        return applies, "" if applies else "目标命中前生命不低于 40%"
    unresolved = {
        "con_targetnotboss": "缺少正式 Boss 分类，非 Boss 条件不推算",
        "con_skia_level3_1": "缺少牙齿状态，觉醒三条件不推算",
        "con_skia_level3_2": "缺少牙齿状态，觉醒三条件不推算",
        "con_mint_lv4": "缺少觉醒四运行时状态，条件增益不推算",
        "con_kuhara_targethaveattachment": "缺少契约目标状态，条件增益不推算",
        "con_radio072_isawake_critup": "灵可觉醒六改由同频合击公式身份适配",
        "con_1072_islevel5": "灵可觉醒五的环合兼容条件尚不能由通用 Buff 推断",
    }
    for marker, reason in unresolved.items():
        if marker in normalized:
            return False, reason
    return True, ""


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
    "character_awaken:1072:Effect6": ConfirmedAwakeningBuffSpec(
        "世界将予你回声（觉醒六）", "team", "STATIC_EQUIPPED_SOURCE", None,
        (("CritBase", 0.25, LINKO_COATTACK_REQUIREMENT),),
    ),
    "character_awaken:1072:resonance_6": ConfirmedAwakeningBuffSpec(
        "万籁同频的刹那（六觉共鸣）", "team",
        "ABILITY_EVENT|Q|GA_Radio072_UltraSkill", 13.0,
        (("DamageUpNatureBase", 0.30), ("DamageUpIncantationBase", 0.30)),
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
    "character_awaken:1072:Effect6",
    "character_awaken:1072:resonance_6",
})


class BattleConfirmedAwakeningBuffService:
    @staticmethod
    def get(effect_definition_id: str) -> ConfirmedAwakeningBuffSpec | None:
        return _SPECS.get(effect_definition_id)

    @staticmethod
    def replaces_generic(effect_definition_id: str) -> bool:
        return effect_definition_id in _REPLACES_GENERIC
