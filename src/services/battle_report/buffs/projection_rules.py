# 独立判断单个冻结命中与 Buff 区间的适用修正和证据理由。
from __future__ import annotations
from dataclasses import dataclass
from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.buffs.models import BattleInferredBuffInterval
from src.services.battle_report.buffs.semantics import calculation_applies_to_damage, specialized_calculation_reason
from src.services.battle_report.characters.awakening import character_awakening_requirement_applies
from src.services.battle_report.characters.passives import passive_requirement_applies
from src.services.battle_report.damage.composition import classify_battle_hit_channel
from src.services.battle_report.environment.outer_realm_buffs import outer_realm_requirement_applies

BUFF_ATTRIBUTE_PROJECTION_VERSION = "battle-buff-attribute-v22"
_INFERRED_HIT_TARGET_PREFIX = "battle-hit-target|id="

_CONTINUOUS_DAMAGE_CHANNELS = frozenset(
    {
        "dot",
        "special_nightmare",
        "special_zankou_erosion",
        "special_zankou_venom",
        "reaction_scorch",
    }
)

_PROPERTY_ALIASES = {
    "Crit": "CritBase",
    "CritAdd": "CritBase",
    "CritDamageAdd": "CritDamageBase",
    "DamageUpGeneralAdd": "DamageUpGeneralBase",
    "DamageUpChaosAdd": "DamageUpChaosBase",
    "DamageUpCosmosAdd": "DamageUpCosmosBase",
    "DamageUpIncantationAdd": "DamageUpIncantationBase",
    "DamageUpLakshanaAdd": "DamageUpLakshanaBase",
    "DamageUpNatureAdd": "DamageUpNatureBase",
    "DamageUpPsycheAdd": "DamageUpPsycheBase",
    "DamageUpPsychicallyAdd": "DamageUpPsychicallyBase",
    "ToppleDamageUp": "UnbalDamageUp",
}
_SAFE_ADDITIVE_PROPERTIES = frozenset(
    {
        "AtkUp",
        "AtkAdd",
        "HPMaxUp",
        "HPMaxAdd",
        "DefUp",
        "DefAdd",
        "CritBase",
        "CritDamageBase",
        "DamageUpGeneralBase",
        "DamageUpChaosBase",
        "DamageUpCosmosBase",
        "DamageUpIncantationBase",
        "DamageUpLakshanaBase",
        "DamageUpNatureBase",
        "DamageUpPsycheBase",
        "DamageUpPsychicallyBase",
        "DefIgnore",
        "DamagePenetrateChaos",
        "DamagePenetrateCosmos",
        "DamagePenetrateIncantation",
        "DamagePenetrateLakshana",
        "DamagePenetrateNature",
        "DamagePenetratePsyche",
        "DamagePenetratePsychically",
        "DamageResistChaosBase",
        "DamageResistChaosAdd",
        "DamageResistCosmosBase",
        "DamageResistCosmosAdd",
        "DamageResistIncantationBase",
        "DamageResistIncantationAdd",
        "DamageResistLakshanaBase",
        "DamageResistLakshanaAdd",
        "DamageResistNatureBase",
        "DamageResistNatureAdd",
        "DamageResistPsycheBase",
        "DamageResistPsycheAdd",
        "DamageResistPsychicallyBase",
        "DamageResistPsychicallyAdd",
        "MagBase",
        "UnbalIntensityBase",
        "UnbalIntensityUp",
        "UnbalIntensityAdd",
        "UnbalDamageUp",
    }
)
_TOPPLE_ONLY_PROPERTIES = frozenset(
    {
        "UnbalIntensityBase",
        "UnbalIntensityUp",
        "UnbalIntensityAdd",
        "UnbalDamageUp",
    }
)
_TOPPLE_CHANNELS = frozenset(
    {
        "other_topple",
        "special_daffodill_extra_topple",
    }
)
_TOPPLE_FORMULA_PROPERTIES = frozenset(
    {
        *_TOPPLE_ONLY_PROPERTIES,
        "DefIgnore",
        *(
            property_id
            for property_id in _SAFE_ADDITIVE_PROPERTIES
            if property_id.startswith("DamagePenetrate") or property_id.startswith("DamageResist")
        ),
    }
)
_ELEMENT_NAMES = {
    "Chaos": "chaos",
    "Cosmos": "cosmos",
    "Incantation": "incantation",
    "Lakshana": "lakshana",
    "Nature": "nature",
    "Psyche": "psyche",
    "Psychically": "psychically",
}
_ELEMENT_PROPERTY_ATTRIBUTE = {
    property_id: damage_type
    for element_name, damage_type in _ELEMENT_NAMES.items()
    for property_id in (
        f"DamageUp{element_name}Base",
        f"DamagePenetrate{element_name}",
        f"DamageResist{element_name}Base",
        f"DamageResist{element_name}Add",
    )
}
_TARGET_PROPERTIES = frozenset(
    property_id for property_id in _SAFE_ADDITIVE_PROPERTIES if property_id.startswith("DamageResist")
)
_CONFIDENCE_ORDER = {"未解析": 0, "低": 1, "中": 2, "高": 3}
_NON_DAMAGE_PROPERTIES = frozenset(
    {
        "ChargeGetEfficiencyBase",
        "DerivedDamageCoefficient",
        "HealUp",
        "HPCurrentReductionRatio",
        "HPCurrentRestoreRatio",
        "ImmuneDeadByTeammates",
        "ShareOutTeammatesDamageMul",
        "ShieldUp",
        "ToppleDurationAdd",
        "UltraEnergyAdd",
        "MoveSpeedMaxMult",
    }
)
_UNRESOLVED_REASON_MARKERS = (
    "作用对象尚未确定",
    "逐击角色未知",
    "缺少目标实例",
    "缺少正式 Boss 分类",
    "缺少牙齿状态",
    "缺少觉醒四运行时状态",
    "缺少契约目标状态",
    "缺少命中前目标生命",
    "缺少正式标签状态",
    "缺少正式目标标签状态",
    "与 Buff 作用对象不匹配",
    "尚未映射到安全伤害乘区",
    "不是已支持的加法修正",
    "数值或 Calculation 尚未解析",
    "没有可计算的属性修正",
)


def normalize_battle_buff_property_id(property_id: str) -> str:
    """Return the formula-facing property used by per-hit Buff projection."""

    return _PROPERTY_ALIASES.get(property_id, property_id)


def _minimum_confidence(*values: str) -> str:
    normalized = tuple(value if value in _CONFIDENCE_ORDER else "低" for value in values)
    return min(normalized, key=_CONFIDENCE_ORDER.__getitem__) if normalized else "未解析"


def _confirmed_source_tags_apply(
    interval: BattleInferredBuffInterval,
    modifier: object,
    hit: BattleAnalysisHit,
) -> tuple[bool, str]:
    target_tags = tuple(getattr(modifier, "target_require_tags", ()) or ())
    if target_tags:
        return False, "缺少正式目标标签状态，条件 Buff 不推算"
    tags = tuple(getattr(modifier, "source_require_tags", ()) or ())
    if not tags:
        return True, ""
    attack_type = hit.attack_type.casefold()
    identity = "|".join(
        (
            hit.attack_type,
            hit.gameplay_effect_id,
            hit.ability_id,
            hit.skill_name,
            hit.damage_name,
        )
    ).casefold()
    channel_id = classify_battle_hit_channel(hit)[0]
    is_melee = (
        attack_type in {"普攻", "普通攻击", "normal", "normalattack", "melee", "a"}
        or "_melee" in hit.ability_id.casefold()
    ) and "ultraskill" not in identity
    is_ultra = attack_type in {"q技能", "ultra"} or "ultraskill" in identity
    requirements = {
        "state.damage": hit.direction == "outgoing",
        "state.damage.skill": (
            attack_type in {"skill", "e技能"} or ("_skill" in identity and "ultraskill" not in identity)
        ),
        "state.damage.ultraskill": is_ultra,
        "state.damage.qte": (attack_type == "qte" or "qte" in identity),
        "state.damage.attachment": ("attachment" in identity or "ge_player_kuhara_budboom_damage" in identity),
        "state.damage.melee": is_melee,
        "state.damage.normalattack": is_melee,
        "state.damage.dot": channel_id in _CONTINUOUS_DAMAGE_CHANNELS,
        "state.damage.unbalance": channel_id in _TOPPLE_CHANNELS,
        "state.damage.perfectevadedamage": ("perfectevade" in identity or "闪避反击" in hit.attack_type),
        "state.cure": False,
        "ability.ultraskill": is_ultra,
        "state.damage.extremecounter": (
            "extrem" in identity or "极限反击" in hit.attack_type or "闪避反击" in hit.attack_type
        ),
        "state.damage.normalorcounter": (
            is_melee or "extrem" in identity or "极限反击" in hit.attack_type or "闪避反击" in hit.attack_type
        ),
    }
    recognized = tuple(requirements[tag.casefold()] for tag in tags if tag.casefold() in requirements)
    ability_requirements = tuple(
        tag.rsplit(".", 1)[-1].casefold() in identity
        for tag in tags
        if tag.casefold().startswith("ability.") and tag.casefold() != "ability.ultraskill"
    )
    supported = set(requirements)
    unsupported = tuple(
        tag for tag in tags if tag.casefold() not in supported and not tag.casefold().startswith("ability.")
    )
    if unsupported:
        return False, "缺少正式标签状态，条件 Buff 不推算"
    if (recognized and not all(recognized)) or (ability_requirements and not all(ability_requirements)):
        return False, "该 Buff 修正只作用于指定技能伤害标签"
    return True, ""


def _inferred_hit_target_applies(
    requirement: str,
    hit: BattleAnalysisHit,
) -> tuple[bool, str]:
    if not requirement.startswith(_INFERRED_HIT_TARGET_PREFIX):
        return True, ""
    target_id = requirement.removeprefix(_INFERRED_HIT_TARGET_PREFIX)
    if hit.target_id != target_id:
        return False, "推断的条件增伤只投影到出现倍率阶跃的同一目标"
    return True, ""


@dataclass(frozen=True, slots=True)
class IntervalProjectionEvaluation:
    candidates: tuple[tuple[str, float, str], ...]
    accepted_properties: tuple[str, ...]
    reasons: tuple[str, ...]


def evaluate_interval_projection(
    hit: BattleAnalysisHit,
    interval: BattleInferredBuffInterval,
    channel_id: str,
) -> IntervalProjectionEvaluation:
    candidates: list[tuple[str, float, str]] = []
    accepted_properties: set[str] = set()
    accepted = False
    interval_reasons: list[str] = []
    source_character_scope = interval.target_scope.startswith("character:")
    if (
        interval.target_scope
        not in {
            "self",
            "team",
            "team_others",
            "target",
        }
        and not source_character_scope
    ):
        interval_reasons.append("作用对象尚未确定")
    elif interval.target_scope == "team_others" and (hit.character_id is None or int(hit.character_id) <= 0):
        interval_reasons.append("逐击角色未知，无法确认该击是否属于来源角色之外的队友")
    elif interval.target_scope == "target" and not interval.target_id:
        interval_reasons.append("缺少目标实例，敌方 Buff/Debuff 不跨目标推算")
    elif interval.target_scope == "target" and interval.target_id != hit.target_id:
        interval_reasons.append("与本击目标实例不匹配")
    else:
        for modifier in interval.modifiers:
            source_applies, source_reason = _confirmed_source_tags_apply(
                interval,
                modifier,
                hit,
            )
            if not source_applies:
                interval_reasons.append(source_reason)
                continue
            requirement = modifier.application_requirement_asset_path.casefold()
            target_applies, target_reason = _inferred_hit_target_applies(
                modifier.application_requirement_asset_path,
                hit,
            )
            if not target_applies:
                interval_reasons.append(target_reason)
                continue
            passive_applies, passive_reason = passive_requirement_applies(
                modifier.application_requirement_asset_path,
                hit,
            )
            if not passive_applies:
                interval_reasons.append(passive_reason)
                continue
            awakening_applies, awakening_reason = character_awakening_requirement_applies(
                modifier.application_requirement_asset_path,
                hit,
            )
            if not awakening_applies:
                interval_reasons.append(awakening_reason)
                continue
            outer_applies, outer_reason = outer_realm_requirement_applies(
                modifier.application_requirement_asset_path,
                hit,
            )
            if not outer_applies:
                interval_reasons.append(outer_reason)
                continue
            if requirement == "battle-channel:continuous-damage" and channel_id not in _CONTINUOUS_DAMAGE_CHANNELS:
                interval_reasons.append("该 Buff 只作用于噩梦、蚀心、鸩火和浊燃等持续伤害")
                continue
            applies_to_hit = calculation_applies_to_damage(
                modifier.calculation_asset_path,
                hit.gameplay_effect_id,
            )
            if applies_to_hit is False:
                interval_reasons.append("该专用倍率只作用于其绑定的伤害项，本击不采用")
                continue
            specialized_reason = specialized_calculation_reason(modifier.calculation_asset_path)
            if applies_to_hit is True and specialized_reason:
                interval_reasons.append(f"{specialized_reason}，由逐击重放适配器单独计算")
                continue
            property_id = normalize_battle_buff_property_id(modifier.property_id)
            if (
                property_id == "CoefModify"
                and interval.source_effect_definition_id == "character_awaken:1036:resonance_3"
                and hit.character_id == 1036
                and hit.classification == "direct"
                and "zankou"
                in "|".join(
                    (
                        hit.ability_id,
                        hit.gameplay_effect_id,
                    )
                ).casefold()
                and "ultraskill"
                in "|".join(
                    (
                        hit.ability_id,
                        hit.gameplay_effect_id,
                    )
                ).casefold()
            ):
                interval_reasons.append("三觉 Q 的 CoefModify 已由正式技能倍率证据消费，不再作为待确认属性重复投影")
                continue
            if property_id in _TOPPLE_ONLY_PROPERTIES and channel_id not in _TOPPLE_CHANNELS:
                interval_reasons.append(f"{property_id} 只进入倾陷伤害的逐角色格子")
                continue
            if channel_id in _TOPPLE_CHANNELS and property_id not in _TOPPLE_FORMULA_PROPERTIES:
                interval_reasons.append(f"{property_id} 不进入倾陷的逐角色公式")
                continue
            if channel_id == "reaction_nova" and property_id not in {
                "MagBase",
                "DamagePenetratePsychically",
                "DamageResistPsychicallyBase",
                "DamageResistPsychicallyAdd",
            }:
                interval_reasons.append(f"{property_id} 不进入黯星的等级、环合强度与抗性公式")
                continue
            operation = modifier.modifier_operation.casefold()
            if property_id not in _SAFE_ADDITIVE_PROPERTIES:
                interval_reasons.append(
                    f"{property_id} 不属于本击伤害公式"
                    if property_id in _NON_DAMAGE_PROPERTIES
                    else f"{property_id} 尚未映射到安全伤害乘区"
                )
                continue
            target_property = property_id in _TARGET_PROPERTIES
            if target_property != (interval.target_scope == "target"):
                interval_reasons.append(f"{property_id} 与 Buff 作用对象不匹配")
                continue
            expected_attribute = _ELEMENT_PROPERTY_ATTRIBUTE.get(property_id)
            if expected_attribute is not None and hit.damage_attribute.casefold() != expected_attribute:
                interval_reasons.append(f"{property_id} 与该击伤害属性不匹配")
                continue
            if not operation.endswith("additive"):
                interval_reasons.append(f"{property_id} 不是已支持的加法修正")
                continue
            if modifier.magnitude_value is None or modifier.value_confidence not in {"中", "高"}:
                interval_reasons.append(f"{property_id} 数值或 Calculation 尚未解析")
                continue
            accepted = True
            accepted_properties.add(property_id)
            confidence = _minimum_confidence(
                interval.state_confidence,
                modifier.value_confidence,
            )
            candidates.append((property_id, float(modifier.magnitude_value), confidence))
    reasons = tuple(dict.fromkeys(interval_reasons or (() if accepted else ("没有可计算的属性修正",))))
    return IntervalProjectionEvaluation(
        tuple(candidates),
        tuple(sorted(accepted_properties)),
        reasons,
    )
