# 将已审计角色培养被动整理为固定轴重放可消费的规则目录。
"""Character-passive catalog and conservative fixed-axis rule adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import BattleAnalysisHit, BattleBuffModifierEvidence


CHARACTER_PASSIVE_MODEL_VERSION = "battle-character-passive-v4"
MITSUKI_GRADUAL_BUFF_IDENTITY = (
    "confirmed:character_passive:1070:mitsuki-gradual-attack"
)


@dataclass(frozen=True, slots=True)
class CharacterPassiveDefinition:
    passive_id: str
    character_id: int
    character_name: str
    ability_id: str
    name: str
    unlock_stage: int
    asset_path: str
    replay_kind: str
    adapter_id: str
    fixed_axis_policy: str


@dataclass(frozen=True, slots=True)
class EnabledCharacterPassive:
    definition: CharacterPassiveDefinition
    source_character_id: int
    source_character_name: str


@dataclass(frozen=True, slots=True)
class CharacterPassiveRuleSpec:
    passive_id: str
    passive_name: str
    source_character_id: int
    source_character_name: str
    source_asset_path: str
    target_scope: str
    event_type: str
    effect_type: str
    duration_policy: str
    duration_seconds: float | None
    modifiers: tuple[BattleBuffModifierEvidence, ...]
    stacking_type: str = "AggregateByTarget"
    stack_limit_count: int = 1
    cooldown_seconds: float | None = None


def _passive(
    character_id: int,
    character_name: str,
    ability_id: str,
    name: str,
    stage: int,
    replay_kind: str,
    adapter_id: str,
    policy: str,
    asset_path: str = "",
) -> CharacterPassiveDefinition:
    return CharacterPassiveDefinition(
        passive_id=f"PASSIVE-{character_id}-{ability_id}",
        character_id=character_id,
        character_name=character_name,
        ability_id=ability_id,
        name=name,
        unlock_stage=stage,
        asset_path=asset_path or f"confirmed:character-passive:{character_id}:{ability_id}",
        replay_kind=replay_kind,
        adapter_id=adapter_id,
        fixed_axis_policy=policy,
    )


_CATALOG = (
    _passive(1003, "早雾", "GA_Sagiri_Passive_1", "可以吃吗？", 2, "target_state", "dot-final-multiplier", "按目标 DOT 种类重建专属最终乘区"),
    _passive(1003, "早雾", "GA_Sagiri_Passive_2", "鬼把戏", 4, "target_state", "control-defense-down", "按成功浮空或压制建立目标减防区间"),
    _passive(1004, "安魂曲", "GA_Lacrimosa_Passive_1", "番茄酱盛宴", 2, "derived_hit", "dissonance-toppled-hit", "保留并重放已观测失谐强化逐击"),
    _passive(1004, "安魂曲", "GA_Lacrimosa_Passive_2", "就要自然醒", 4, "action_resource", "lacrimosa-extra-e", "固定轴校验第五次 A 后的额外 E 可用性"),
    _passive(1008, "翳", "GA_Skia_Passive_1", "现场控制", 2, "target_state", "delay-replace", "旧延滞结算后按目标建立新延滞"),
    _passive(1008, "翳", "GA_Skia_Passive_2", "捉拿归案", 4, "timed_modifier", "skia-shadow-damage", "Q 后十五秒仅投影兽牙影刺通伤"),
    _passive(1010, "娜娜莉", "GA_Nanally_Passive_1", "不止「一腔」的热血", 2, "action_lifecycle", "creation-volley", "保留创生花命中并登记十花与一秒间隔"),
    _passive(1010, "娜娜莉", "GA_Nanally_Passive_2", "绝对「公正」的决斗", 4, "derived_hit", "nanally-reaction-follow-up", "按环合事件与两秒冷却归因追加攻击"),
    _passive(1019, "薄荷", "GA_Mint_Passive_1", "变身！超级薄荷！", 2, "spatial_hit", "creation-radius", "单目标收益为零，多目标缺位置时保留真实命中"),
    _passive(1019, "薄荷", "GA_Mint_Passive_2", "收工！宾果时间！", 4, "front_state", "mint-front-defense", "按驻场区间投影防御，抗打断不进入伤害"),
    _passive(1020, "哈尼娅", "GA_Haniel_Passive_1", "是友情啊", 2, "target_state", "dark-star-attack-drain", "按目标黯星结束累计全队固定攻击"),
    _passive(1020, "哈尼娅", "GA_Haniel_Passive_2", "是羁绊啊", 4, "derived_hit", "haniel-hero-aura", "按合奏层数重放魔法炮弹射逐击"),
    _passive(1021, "埃德嘉", "GA_Edgar_Passive_1", "温和的锋芒", 2, "action_resource", "edgar-charge-reaction", "记录盈蓄瞬时回能与三十秒冷却"),
    _passive(1021, "埃德嘉", "GA_Edgar_Passive_2", "不变的暖意", 4, "action_resource", "edgar-truth-key", "按 E/QTE 获钥并延长 Q 领域"),
    _passive(1023, "白藏", "GA_Cang_Passive_1", "适度恶趣味", 2, "target_state", "scorch-refresh", "言灵字结束旧浊燃并重建同伤害周期"),
    _passive(1023, "白藏", "GA_Cang_Passive_2", "适度上工", 4, "static_modifier", "cang-team-cooperation", "常驻攻击直接投影，协同击按原轴归因"),
    _passive(1025, "哈索尔", "GA_Hathor_Passive_1", "延时预警", 2, "target_state", "delay-critical", "按目标延滞区间投影全队暴击率"),
    _passive(1025, "哈索尔", "GA_Hathor_Passive_2", "效率推进", 4, "action_resource", "hathor-delivery-stack", "按本人击杀维护闪送层和分段 E 可行性"),
    _passive(1033, "阿德勒", "GA_Adler_Passive_1", "克己", 2, "target_state", "adler-random-debuff", "缺运行时随机结果时按三种效果各三分之一估计"),
    _passive(1033, "阿德勒", "GA_Adler_Passive_2", "正心", 4, "static_modifier", "adler-defense", "常驻防御进入防御倍率技能"),
    _passive(1036, "残虹", "GA_Zankou_Passive1", "暮落残阳", 2, "target_state", "zankou-scorch-stack", "按目标 DOT 施加重建三层浊燃"),
    _passive(1036, "残虹", "GA_Zankou_Passive2", "殷红幻景", 4, "static_modifier", "zankou-ring-strength", "常驻环合强度直接投影，开场环合值单独保存"),
    _passive(1039, "法帝娅", "GA_Fadia_Passive_1", "罪感熔炉", 2, "target_state", "fadia-max-hp-drain", "复用黯星结束最大生命适配器"),
    _passive(1039, "法帝娅", "GA_Fadia_Passive_2", "拒斥与豪掠", 4, "static_modifier", "fadia-team-hp", "全队最大生命百分比直接投影"),
    _passive(1046, "「零」", "GA_Female_Passive_1", "鉴定师", 2, "healing", "charge-heal", "按正向终结能量事件记录治疗，不混入伤害"),
    _passive(1046, "「零」", "GA_Female_Passive_2", "异象感知力", 4, "skill_modifier", "protagonist-q-damage", "仅零的极轨终结获得通伤"),
    _passive(1052, "浔", "GA_Jin_Passive_1", "鬼兰家纹", 2, "action_lifecycle", "creation-time-stop", "保留时停内创生逐击，不反事实移除常驻被动"),
    _passive(1052, "浔", "GA_Jin_Passive_2", "天下万宝", 4, "skill_multiplier", "jin-q-terminal", "终结段基础倍率乘二"),
    _passive(1054, "达芙蒂尔", "GA_Daffodill_Passive_1", "破鞘", 2, "target_state", "dissonance-topple-cap", "按目标两层整组刷新倾陷上限"),
    _passive(1054, "达芙蒂尔", "GA_Daffodill_Passive_2", "空蝉", 4, "skill_modifier", "daffodill-entry-damage", "仅幻影移行获得通伤"),
    _passive(1055, "九原", "GA_Kuhara_Passive_1", "顺势而获", 2, "action_lifecycle", "creation-cap", "固定轴保留创生逐击并登记双株与六株上限"),
    _passive(1055, "九原", "GA_Kuhara_Passive_2", "风声为我所用", 4, "derived_hit", "kuhara-rose-settlement", "按目标玫约状态归因十五倍率追加清算"),
    _passive(1070, "海月", "GA_Mitsuki_Passive1", "泛音", 2, "derived_hit", "dark-star-triple-hit", "每个目标黯星结束归因三次海月逐击"),
    _passive(1070, "海月", "GA_Mitsuki_Passive2", "渐强", 4, "stack_modifier", "mitsuki-jellyfish-stack", "水母弹逐击后加层并整组刷新五秒"),
    _passive(1071, "卡厄斯", "GA_Chaos_Passive_1", "未迟到的正义", 2, "derived_hit", "delay-end-damage", "按目标延滞实际时长重放结束伤害"),
    _passive(1071, "卡厄斯", "GA_Chaos_Passive_2", "重点关注！", 4, "target_state", "pursuit-license", "追缉许可对卡厄斯本人的通伤替换为百分之三十"),
    _passive(1072, "灵可", "GA_Radio072_Passive_1", "弱点感应", 2, "reaction_formula", "lingke-follow-up", "覆纹追加倍率与限定通伤进入反应适配器"),
    _passive(1072, "灵可", "GA_Radio072_Passive_2", "精确调频", 4, "target_state", "same-frequency-resistance", "按目标和触发属性建立十二秒减抗"),
    _passive(1073, "小吱", "GA_Chiichan073_Passive_1", "飞鸟症候群", 2, "action_resource", "charge-reaction-add", "盈蓄基础值先加四再乘充能效率"),
    _passive(1073, "小吱", "GA_Chiichan073_Passive_2", "囤积癖", 4, "front_state", "chiichan-charge-efficiency", "仅驻场时投影充能效率"),
    _passive(1075, "伊洛伊", "GA_Oneiroi_Passive_1", "镜象", 2, "action_lifecycle", "creation-copy", "按三秒生成复制株及二十朵复制花"),
    _passive(1075, "伊洛伊", "GA_Oneiroi_Passive_2", "交感性神经系统", 4, "timed_modifier", "oneiroi-heal-defense-ignore", "每次治疗刷新全队二十秒无视防御"),
    _passive(1076, "真红", "GA_Shinku_Passive_1", "独行", 2, "derived_hit", "shinku-charge-reaction", "按盈蓄事件与一秒冷却归因范围击和攻击层"),
    _passive(1076, "真红", "GA_Shinku_Passive_2", "逆鳞", 4, "skill_modifier", "shinku-q-non-boss", "仅非 Boss 目标的极轨终结获得通伤"),
)


_DIRECT_RULES: dict[str, tuple[dict[str, Any], ...]] = {
    "PASSIVE-1008-GA_Skia_Passive_2": ({
        "scope": "self", "event": "ABILITY_EVENT_END|Q", "duration": 15.0,
        "modifiers": (("DamageUpGeneralBase", 0.10, "battle-passive|ge-prefix-any=GE_Player_Skia_ShadowAtk,GE_Player_Skia_SkillShadowAtk"),),
    },),
    "PASSIVE-1019-GA_Mint_Passive_2": (
        {"scope": "self", "event": "BUFF_EVENT_CHANGE_ROLE_IN_BEGIN", "duration_policy": "Infinite", "modifiers": (("DefUp", 0.20, ""),)},
        {"scope": "self", "event": "BUFF_EVENT_CHANGE_ROLE_OUT_BEGIN", "effect": "REMOVE", "duration_policy": "Instant", "modifiers": ()},
    ),
    "PASSIVE-1023-GA_Cang_Passive_2": ({"scope": "self", "event": "PASSIVE_STATIC", "modifiers": (("AtkUp", 0.20, ""),)},),
    "PASSIVE-1033-GA_Adler_Passive_2": ({"scope": "self", "event": "PASSIVE_STATIC", "modifiers": (("DefUp", 0.20, ""),)},),
    "PASSIVE-1036-GA_Zankou_Passive2": ({"scope": "self", "event": "PASSIVE_STATIC", "modifiers": (("MagBase", 100.0, ""),)},),
    "PASSIVE-1039-GA_Fadia_Passive_2": ({"scope": "team", "event": "PASSIVE_STATIC", "modifiers": (("HPMaxUp", 0.10, ""),)},),
    "PASSIVE-1046-GA_Female_Passive_2": ({
        "scope": "self", "event": "PASSIVE_STATIC",
        "modifiers": (("DamageUpGeneralBase", 0.25, "battle-passive|ability-prefix-any=GA_Female046_UltraSkill,GA_Female051_UltraSkill"),),
    },),
    "PASSIVE-1054-GA_Daffodill_Passive_2": ({
        "scope": "self", "event": "PASSIVE_STATIC",
        "modifiers": (("DamageUpGeneralBase", 0.80, "battle-passive|ge-prefix-any=GE_Player_Daffodill_EntryAttack"),),
    },),
    "PASSIVE-1070-GA_Mitsuki_Passive2": ({
        "scope": "self",
        "event": (
            "PASSIVE_HIT|GE_Player_Mitsuki_PerfectAtkBullet,水母弹,jellyfish"
        ),
        "duration": 5.0,
        "modifiers": (("AtkUp", 0.01, ""),),
        "stacking_type": "AggregateBySource+RefreshWholeStack",
        "stack_limit_count": 10,
    },),
    "PASSIVE-1076-GA_Shinku_Passive_1": ({
        "scope": "self",
        "event": "PASSIVE_HIT|GE_Player_Shinku_ReactionAOE_Damage",
        "duration": 30.0,
        "modifiers": (("AtkUp", 0.05, ""),),
        "stacking_type": "AggregateBySource+RefreshWholeStack",
        "stack_limit_count": 10,
        "cooldown_seconds": 1.0,
    },),
    "PASSIVE-1072-GA_Radio072_Passive_1": ({
        "scope": "team",
        "event": "PASSIVE_STATIC",
        "modifiers": ((
            "DamageUpGeneralBase",
            0.10,
            "battle-passive|follow-up-consumer=true;target-weave=true",
        ),),
    },),
}


def _stage(character: Mapping[str, Any]) -> int:
    profile = character.get("profile") or {}
    return int(character.get("breakthrough_stage") or profile.get("breakthrough_stage") or 0)


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


def passive_requirement_applies(
    requirement: str,
    hit: BattleAnalysisHit,
) -> tuple[bool, str]:
    """Evaluate the narrow, explainable hit consumer marker used by passives."""

    if not requirement.casefold().startswith("battle-passive|"):
        return True, ""
    conditions = requirement.split("|", 1)[1].split(";")
    for condition in conditions:
        key, separator, raw = condition.partition("=")
        values = tuple(value.casefold() for value in raw.split(",") if value)
        if not separator or not values:
            return False, "角色被动消费者条件无法解析"
        if key == "ability-prefix-any":
            actual = hit.ability_id.casefold()
            matched = any(actual.startswith(value) for value in values)
            label = "技能"
        elif key == "ge-prefix-any":
            actual = hit.gameplay_effect_id.casefold()
            matched = any(actual.startswith(value) for value in values)
            label = "伤害项"
        elif key == "formal-follow-up":
            actual = hit.is_formal_follow_up
            matched = str(actual).casefold() in values
            label = "正式追加攻击身份"
        elif key == "follow-up-consumer":
            actual = (
                not hit.is_follow_up
                and hit.classification != "weave"
                and (
                    hit.is_formal_follow_up
                    or hit.formula_context_kind.startswith("linko_coattack:")
                )
            )
            matched = str(actual).casefold() in values
            label = "追加攻击公式消费者身份"
        elif key == "target-weave":
            actual = hit.target_has_weave
            matched = str(actual).casefold() in values
            label = "目标覆纹状态"
        else:
            return False, f"角色被动消费者条件 {key} 尚未支持"
        if not matched:
            return False, f"该角色被动只作用于指定{label}"
    return True, ""


class BattleCharacterPassiveService:
    """Expose all audited passives and only materialize bounded formula rules."""

    @staticmethod
    def catalog() -> tuple[CharacterPassiveDefinition, ...]:
        return _CATALOG

    @staticmethod
    def is_unlocked(
        build: Mapping[str, Any] | None,
        character_id: int,
        unlock_stage: int,
    ) -> bool:
        logical_id = 1046 if character_id == 1051 else character_id
        for character in (build or {}).get("characters") or ():
            source_id = int(character.get("character_id") or 0)
            source_logical_id = 1046 if source_id == 1051 else source_id
            if source_logical_id == logical_id:
                return _stage(character) >= unlock_stage
        return False

    @classmethod
    def enabled_passives(
        cls,
        build: Mapping[str, Any] | None,
    ) -> tuple[EnabledCharacterPassive, ...]:
        catalog_by_character: dict[int, list[CharacterPassiveDefinition]] = {}
        for definition in cls.catalog():
            catalog_by_character.setdefault(definition.character_id, []).append(definition)
        result = []
        for character in (build or {}).get("characters") or ():
            source_id = int(character.get("character_id") or 0)
            logical_id = 1046 if source_id == 1051 else source_id
            source_name = str(character.get("observed_name") or logical_id)
            for definition in catalog_by_character.get(logical_id, ()):
                if _stage(character) >= definition.unlock_stage:
                    result.append(EnabledCharacterPassive(
                        definition=definition,
                        source_character_id=source_id,
                        source_character_name=source_name,
                    ))
        return tuple(result)

    @classmethod
    def rule_specs(
        cls,
        build: Mapping[str, Any] | None,
    ) -> tuple[CharacterPassiveRuleSpec, ...]:
        result = []
        for enabled in cls.enabled_passives(build):
            definition = enabled.definition
            source_character = next(
                (
                    row for row in (build or {}).get("characters") or ()
                    if int(row.get("character_id") or 0)
                    == enabled.source_character_id
                ),
                {},
            )
            for raw in _DIRECT_RULES.get(definition.passive_id, ()):
                modifiers = tuple(
                    BattleBuffModifierEvidence(
                        property_id=property_id,
                        modifier_operation="EGameplayModOp::Additive",
                        magnitude_kind="confirmed_character_passive",
                        magnitude_value=float(value),
                        calculation_asset_path="",
                        value_confidence="高",
                        application_requirement_asset_path=requirement,
                    )
                    for property_id, value, requirement in raw.get("modifiers", ())
                )
                result.append(CharacterPassiveRuleSpec(
                    passive_id=definition.passive_id,
                    passive_name=definition.name,
                    source_character_id=enabled.source_character_id,
                    source_character_name=enabled.source_character_name,
                    source_asset_path=definition.asset_path,
                    target_scope=str(raw["scope"]),
                    event_type=str(raw["event"]),
                    effect_type=str(raw.get("effect") or "ADD"),
                    duration_policy=str(raw.get("duration_policy") or ("HasDuration" if raw.get("duration") else "Infinite")),
                    duration_seconds=(None if raw.get("duration") is None else float(raw["duration"])),
                    modifiers=modifiers,
                    stacking_type=str(raw.get("stacking_type") or "AggregateByTarget"),
                    stack_limit_count=(
                        20
                        if definition.passive_id
                        == "PASSIVE-1070-GA_Mitsuki_Passive2"
                        and _awakening_enabled(source_character, "Effect6")
                        else max(1, int(raw.get("stack_limit_count") or 1))
                    ),
                    cooldown_seconds=(
                        None
                        if raw.get("cooldown_seconds") is None
                        else float(raw["cooldown_seconds"])
                    ),
                ))
        return tuple(result)

    @classmethod
    def load_rules(cls, build: Mapping[str, Any] | None, rule_type: Any) -> tuple[Any, ...]:
        return tuple(
            rule_type(
                rule_id=f"{row.passive_id}:direct:{ordinal}",
                source_effect_definition_id=(
                    f"character_passive:{row.source_character_id}:"
                    f"{row.passive_id.split('-', 2)[-1]}"
                ),
                source_kind="confirmed_character_passive",
                source_character_id=row.source_character_id,
                source_character_name=row.source_character_name,
                source_asset_path=row.source_asset_path,
                target_asset_path=(
                    MITSUKI_GRADUAL_BUFF_IDENTITY
                    if row.passive_id == "PASSIVE-1070-GA_Mitsuki_Passive2"
                    else f"confirmed:{row.passive_id}"
                ),
                target_name=row.passive_name,
                target_scope=row.target_scope,
                event_type=row.event_type,
                effect_type=row.effect_type,
                duration_policy=row.duration_policy,
                duration_seconds=row.duration_seconds,
                stack_count=1,
                modifiers=row.modifiers,
                stacking_type=row.stacking_type,
                stack_limit_count=row.stack_limit_count,
                cooldown_seconds=row.cooldown_seconds,
            )
            for ordinal, row in enumerate(cls.rule_specs(build))
        )

    @staticmethod
    def skill_multiplier_adjustment(
        character: Mapping[str, Any],
        *,
        damage_id: str,
        ability_id: str,
    ) -> tuple[float, str]:
        character_id = int(character.get("character_id") or 0)
        if (
            character_id == 1052
            and _stage(character) >= 4
            and ability_id == "GA_Jin_UltraSkill"
            and damage_id == "GE_Player_Jin_UltraSkill3_Damage"
        ):
            return 2.0, "突破被动「天下万宝」：极轨终结的终结段基础倍率 ×2"
        return 1.0, ""
