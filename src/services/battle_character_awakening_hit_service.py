# 将已确认的角色觉醒条件收窄到对应正式逐击，不扩散为常驻面板属性。
"""Per-hit gates for character awakening modifiers."""

from __future__ import annotations

from typing import Any, Mapping

from src.domain.battle_report import BattleAnalysisHit


ZERO_FIRST_GAZE_REQUIREMENT = "battle-awakening:zero-first-gaze-extra-hit"
FADIA_GODSLAYER_REQUIREMENT = "battle-awakening:fadia-godslayer"
MITSUKI_ULTRA_REQUIREMENT = "battle-awakening:mitsuki-ultra"
LINKO_COATTACK_REQUIREMENT = "battle-awakening:linko-coattack"

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


def _awakening_enabled(character: Mapping[str, Any], effect_id: str) -> bool:
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
) -> tuple[float, str]:
    """Return explicit per-hit awakening multipliers backed by formal effects."""

    if (
        int(character.get("character_id") or 0) == 1055
        and damage_id.casefold() == _KUHARA_ATTACHMENT_DAMAGE_ID
        and _awakening_enabled(character, "Effect2")
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
        if _awakening_enabled(character, "Effect2")
        else _LINKO_ULTRA_BASE_DAMAGE_ID
    )


def character_awakening_requirement_applies(
    requirement: str,
    hit: BattleAnalysisHit,
) -> tuple[bool, str]:
    """Return whether one confirmed awakening modifier belongs to this hit."""

    normalized = str(requirement or "").casefold()
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
