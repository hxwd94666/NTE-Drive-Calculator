# 将正式触发器条件收窄到可以由战报动作或逐击直接证明的事件。
"""Conservative adapters for source-trigger application requirements."""

from __future__ import annotations

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction


_MITSUKI_LEVEL6 = "con_mitsuki_lv6"
_SHINKU_ULTRA_DAMAGE = "con_shinku_curisultradamage"
_SERVER_ONLY = (
    "con_isserverorstandalone",
    "con_selfisserverorstandalone",
    "con_isonlyserver",
)


def trigger_requirement_applies_to_action(
    requirement: str,
    action: BattleInferredAction,
) -> bool:
    """Return whether an inferred action proves the formal trigger condition."""

    normalized = str(requirement or "").casefold()
    if not normalized:
        return True
    if any(marker in normalized for marker in _SERVER_ONLY):
        return True
    if _MITSUKI_LEVEL6 in normalized:
        return action.input_kind == "E"
    if _SHINKU_ULTRA_DAMAGE in normalized:
        return action.input_kind == "Q"
    if "con_perfectevadedamage" in normalized:
        return action.input_kind == "PERFECT_EVADE" or "闪避" in action.action_name
    if "con_selfisnotusemelee" in normalized:
        return action.input_kind != "A"
    return False


def trigger_requirement_applies_to_hit(
    requirement: str,
    hit: BattleAnalysisHit,
) -> bool:
    """Return whether one immutable hit proves the formal trigger condition."""

    normalized = str(requirement or "").casefold()
    if not normalized:
        return True
    if any(marker in normalized for marker in _SERVER_ONLY):
        return True
    identity = "|".join((
        hit.attack_type,
        hit.ability_id,
        hit.gameplay_effect_id,
        hit.skill_name,
        hit.damage_name,
    )).casefold()
    if _MITSUKI_LEVEL6 in normalized:
        return hit.attack_type.casefold() in {"skill", "e技能"} or (
            "_skill" in hit.ability_id.casefold()
            and "ultraskill" not in hit.ability_id.casefold()
        )
    if _SHINKU_ULTRA_DAMAGE in normalized:
        return hit.attack_type.casefold() in {"ultra", "q技能"} or (
            "ultraskill" in identity
        )
    if "con_perfectevadedamage" in normalized:
        return "perfectevade" in identity or "闪避反击" in hit.attack_type
    if "con_selfisnotusemelee" in normalized:
        return not (
            hit.attack_type in {"普攻", "普通攻击"}
            or "_melee" in hit.ability_id.casefold()
        )
    return False
