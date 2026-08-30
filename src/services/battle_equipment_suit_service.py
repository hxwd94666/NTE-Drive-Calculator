# 空幕二/四件套的固定轴反事实目录与已审计运行时规则。
"""Audited equipment-suit rules for fixed-axis battle replay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import BattleBuffModifierEvidence


EQUIPMENT_SUIT_MODEL_VERSION = "battle-equipment-suit-v2"


@dataclass(frozen=True, slots=True)
class EquipmentSuitEffectDefinition:
    audit_id: str
    suit_id: str
    suit_name: str
    required_count: int
    replay_kind: str
    fixed_axis_policy: str


@dataclass(frozen=True, slots=True)
class _RuleSpec:
    suffix: str
    name: str
    scope: str
    event: str
    duration: float | None
    property_id: str
    value: float
    stacking_type: str = "AggregateByTarget"
    stack_limit_count: int = 1
    stack_count: int = 1


_SUIT_NAMES = {
    "Suit1": "「迪亚波罗斯」",
    "Suit2": "「森林萤火之心」",
    "Suit3": "「恶魔之血：诅咒」",
    "Suit4": "「真红：双生蝶」",
    "Suit5": "「街头拳王」",
    "Suit6": "「失落光芒」",
    "Suit7": "「守卫王国」",
    "Suit8": "「影之信条」",
    "Suit9": "「缇娅的夜间酒馆」",
    "Suit10": "「小小大冒险」",
    "Suit11": "「音速蓝刺猬」",
    "Suit12": "「静谧山庄」",
}

_FOUR_PIECE_POLICIES = {
    "Suit1": ("reaction_state", "常驻12%暗穿；参与黯星或浊燃后追加12%，持续20秒"),
    "Suit2": ("team_hit_stack", "每次已观测灵属性伤害叠一层8%暴伤，独立持续10秒，至多7层"),
    "Suit3": ("target_state", "常驻18%通伤；按同目标已推算黯星/浸染区间追加18%"),
    "Suit4": ("team_hit_stack", "每次已观测咒属性伤害叠一层6%攻击，独立持续10秒，至多6层"),
    "Suit5": ("reaction_state", "常驻14%暴击；队伍触发延滞或浸染后追加14%，持续20秒"),
    "Suit6": ("action_buff", "装备者Q开始后获得25%防御无视，持续20秒"),
    "Suit7": ("non_damage", "护盾效率不直接改写固定伤害轴"),
    "Suit8": ("action_buff", "装备者E完整结束后获得25%攻击，持续20秒"),
    "Suit9": ("non_damage", "治疗效率不直接改写固定伤害轴"),
    "Suit10": ("partial_runtime", "Q开始直接获得10层；普通扣血叠层等待角色受击证据"),
    "Suit11": ("team_action_buff", "装备者Q开始后全队获得15%攻击，持续20秒"),
    "Suit12": ("hit_stack", "每个普通攻击有效Hit后叠一层12%心灵增伤，独立持续6秒，至多3层"),
}

_CATALOG = tuple(
    EquipmentSuitEffectDefinition(
        audit_id=f"SUIT-{suit_id}-{required_count}",
        suit_id=suit_id,
        suit_name=suit_name,
        required_count=required_count,
        replay_kind=(
            "static_modify_pack"
            if required_count == 2
            else _FOUR_PIECE_POLICIES[suit_id][0]
        ),
        fixed_axis_policy=(
            "从正式 modify_pack 读取常驻面板修正"
            if required_count == 2
            else _FOUR_PIECE_POLICIES[suit_id][1]
        ),
    )
    for suit_id, suit_name in _SUIT_NAMES.items()
    for required_count in (2, 4)
)

_DYNAMIC_RULES: dict[str, tuple[_RuleSpec, ...]] = {
    "equipment_suit:Suit1:4": (
        _RuleSpec(
            "base", "迪亚波罗斯：暗属性穿透", "self",
            "STATIC_EQUIPPED_SOURCE", None, "DamagePenetrateChaos", 0.12,
        ),
        _RuleSpec(
            "powered-extra", "迪亚波罗斯：反应后暗属性穿透", "self",
            "SUIT_SOURCE_REACTION_AFTER|reaction_nova:5,reaction_scorch:0",
            20.0, "DamagePenetrateChaos", 0.12,
        ),
    ),
    "equipment_suit:Suit2:4": (
        _RuleSpec(
            "nature-stack", "森林萤火之心：暴击伤害提升", "self",
            "SUIT_TEAM_ATTRIBUTE_HIT|nature", 10.0, "CritDamageBase", 0.08,
            "AggregateBySource", 7,
        ),
    ),
    "equipment_suit:Suit3:4": (
        _RuleSpec(
            "base", "恶魔之血：伤害提升", "self",
            "STATIC_EQUIPPED_SOURCE", None, "DamageUpGeneralBase", 0.18,
        ),
        _RuleSpec(
            "dark-star-target-extra", "恶魔之血：目标状态伤害提升", "self",
            "SUIT_TARGET_STATE_BACKFILL|reaction_nova:5", 5.0,
            "DamageUpGeneralBase", 0.18,
        ),
        _RuleSpec(
            "stain-target-extra", "恶魔之血：目标状态伤害提升", "self",
            "SUIT_TARGET_STATE_FORWARD|reaction_stain:12", 12.0,
            "DamageUpGeneralBase", 0.18,
        ),
    ),
    "equipment_suit:Suit4:4": (
        _RuleSpec(
            "incantation-stack", "真红：双生蝶：攻击力提升", "self",
            "SUIT_TEAM_ATTRIBUTE_HIT|incantation", 10.0, "AtkUp", 0.06,
            "AggregateBySource", 6,
        ),
    ),
    "equipment_suit:Suit5:4": (
        _RuleSpec(
            "base", "街头拳王：暴击率提升", "self",
            "STATIC_EQUIPPED_SOURCE", None, "CritBase", 0.14,
        ),
        _RuleSpec(
            "reaction-extra", "街头拳王：反应后暴击率提升", "self",
            "SUIT_TEAM_REACTION_AFTER|reaction_remora:5,reaction_stain:0",
            20.0, "CritBase", 0.14,
        ),
    ),
    "equipment_suit:Suit6:4": (
        _RuleSpec(
            "q-buff", "失落光芒：防御无视", "self",
            "ABILITY_EVENT|Q|equipment-suit", 20.0, "DefIgnore", 0.25,
        ),
    ),
    "equipment_suit:Suit8:4": (
        _RuleSpec(
            "e-buff", "影之信条：攻击力提升", "self",
            "ABILITY_EVENT_END|E|equipment-suit", 20.0, "AtkUp", 0.25,
        ),
    ),
    "equipment_suit:Suit10:4": (
        _RuleSpec(
            "q-ten-stacks", "小小大冒险：生命上限提升", "self",
            "ABILITY_EVENT|Q|equipment-suit", 10.0, "HPMaxUp", 0.04,
            "AggregateBySource", 10, 10,
        ),
    ),
    "equipment_suit:Suit11:4": (
        _RuleSpec(
            "team-q-buff", "音速蓝刺猬：全队攻击力提升", "team",
            "ABILITY_EVENT|Q|equipment-suit", 20.0, "AtkUp", 0.15,
        ),
    ),
    "equipment_suit:Suit12:4": (
        _RuleSpec(
            "normal-stack", "静谧山庄：心灵伤害提升", "self",
            "SUIT_SOURCE_ATTACK_HIT|A", 6.0,
            "DamageUpPsychicallyBase", 0.12, "AggregateBySource", 3,
        ),
    ),
}

_NO_DAMAGE_RULE_IDS = frozenset({
    "equipment_suit:Suit7:4",
    "equipment_suit:Suit9:4",
})


def _modifier(property_id: str, value: float) -> BattleBuffModifierEvidence:
    return BattleBuffModifierEvidence(
        property_id=property_id,
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind="confirmed_static_curve",
        magnitude_value=value,
        calculation_asset_path="",
        value_confidence="高",
    )


class BattleEquipmentSuitService:
    """Materialize only audited suit rules from the frozen active suit set."""

    @staticmethod
    def catalog() -> tuple[EquipmentSuitEffectDefinition, ...]:
        return _CATALOG

    @staticmethod
    def _modify_pack_rule(
        static_dao: Any,
        selected: Any,
        rule_type: Any,
    ) -> Any | None:
        definition = selected.definition or {}
        parameters = definition.get("parameters") or {}
        if not isinstance(parameters, Mapping):
            return None
        modify_pack_id = str(parameters.get("modify_pack_id") or "").strip()
        if not modify_pack_id or modify_pack_id.casefold() == "none":
            return None
        pack = static_dao.get_equipment_modify_pack(modify_pack_id)
        modifiers = tuple(
            BattleBuffModifierEvidence(
                property_id=str(row.get("property_id") or ""),
                modifier_operation=str(row.get("operation") or "unknown"),
                magnitude_kind="constant",
                magnitude_value=float(row.get("value") or 0.0),
                calculation_asset_path="",
                value_confidence="高",
            )
            for row in (pack or {}).get("modifiers") or ()
            if str(row.get("property_id") or "").strip()
        )
        if not modifiers:
            return None
        effect_id = selected.effect_definition_id
        return rule_type(
            rule_id=f"{effect_id}:modify-pack",
            source_effect_definition_id=effect_id,
            source_kind="equipment_suit_modify_pack",
            source_character_id=selected.character_id,
            source_character_name=selected.character_name,
            source_asset_path=f"combat-effect:{effect_id}",
            target_asset_path=f"modify-pack:{modify_pack_id}",
            target_name=str(definition.get("description_zh") or modify_pack_id),
            target_scope="self",
            event_type="STATIC_EQUIPPED_SOURCE",
            effect_type="ADD",
            duration_policy="Equipped",
            duration_seconds=None,
            stack_count=1,
            modifiers=modifiers,
        )

    @classmethod
    def load_rules(
        cls,
        static_dao: Any,
        selected_effects: Sequence[Any],
        rule_type: Any,
    ) -> tuple[Any, ...]:
        rules = []
        for selected in selected_effects:
            effect_id = str(selected.effect_definition_id)
            if not effect_id.startswith("equipment_suit:"):
                continue
            if effect_id in _NO_DAMAGE_RULE_IDS:
                rule = cls._modify_pack_rule(static_dao, selected, rule_type)
                if rule is not None:
                    rules.append(rule)
                continue
            if effect_id.endswith(":2"):
                rule = cls._modify_pack_rule(static_dao, selected, rule_type)
                if rule is not None:
                    rules.append(rule)
                continue
            for spec in _DYNAMIC_RULES.get(effect_id, ()):
                rules.append(rule_type(
                    rule_id=f"{effect_id}:{spec.suffix}",
                    source_effect_definition_id=effect_id,
                    source_kind="confirmed_equipment_suit",
                    source_character_id=selected.character_id,
                    source_character_name=selected.character_name,
                    source_asset_path=f"combat-effect:{effect_id}",
                    target_asset_path=f"confirmed:{effect_id}:{spec.suffix}",
                    target_name=spec.name,
                    target_scope=spec.scope,
                    event_type=spec.event,
                    effect_type="ADD",
                    duration_policy=("HasDuration" if spec.duration else "Equipped"),
                    duration_seconds=spec.duration,
                    stack_count=spec.stack_count,
                    modifiers=(_modifier(spec.property_id, spec.value),),
                    stacking_type=spec.stacking_type,
                    stack_limit_count=spec.stack_limit_count,
                ))
        return tuple(rules)
