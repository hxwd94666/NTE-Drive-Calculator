# 验证空幕套装的静态属性和逐击触发规则。
from __future__ import annotations

from dataclasses import dataclass, replace

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_buff_inference_service import (
    BattleBuffInferenceService,
    BattleStaticBuffRule,
)
from src.services.battle_equipment_suit_service import BattleEquipmentSuitService


@dataclass(frozen=True)
class _Selected:
    character_id: int
    character_name: str
    effect_definition_id: str
    definition: dict[str, object]


class _StaticDao:
    def get_equipment_modify_pack(self, modify_pack_id: str):
        values = {
            "Chaos_2": ("DamageUpChaosBase", 0.10),
            "Shield_4": ("ShieldEfficiency", 0.20),
            "Heal_4": ("HealUp", 0.20),
        }
        if modify_pack_id not in values:
            return None
        property_id, value = values[modify_pack_id]
        return {
            "modifiers": ({
                "property_id": property_id,
                "operation": "EGameplayModOp::Additive",
                "value": value,
            },),
        }


def _selected(suit_id: str, pieces: int, **parameters: object) -> _Selected:
    return _Selected(
        character_id=1004,
        character_name="安魂曲",
        effect_definition_id=f"equipment_suit:{suit_id}:{pieces}",
        definition={"parameters": {"required_count": pieces, **parameters}},
    )


def _hit(
    event_id: str,
    time_us: int,
    *,
    character_id: int = 1003,
    damage_attribute: str = "nature",
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=time_us,
        relative_time_us=time_us,
        character_id=character_id,
        character_name=str(character_id),
        skill_name="测试技能",
        damage_name="测试伤害",
        damage_component="",
        attack_type="E技能",
        damage_attribute=damage_attribute,
        target_id="target:1",
        target_name="目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
    )


def test_catalog_covers_every_released_suit_and_both_piece_thresholds():
    catalog = BattleEquipmentSuitService.catalog()

    assert len(catalog) == 24
    assert {row.suit_id for row in catalog} == {
        f"Suit{ordinal}" for ordinal in range(1, 13)
    }
    assert {
        (row.suit_id, row.required_count) for row in catalog
    } == {
        (f"Suit{ordinal}", required_count)
        for ordinal in range(1, 13)
        for required_count in (2, 4)
    }


def test_two_piece_rule_uses_the_static_modify_pack_value():
    rules = BattleEquipmentSuitService.load_rules(
        _StaticDao(),
        (_selected("Suit1", 2, modify_pack_id="Chaos_2"),),
        BattleStaticBuffRule,
    )

    assert len(rules) == 1
    assert rules[0].event_type == "STATIC_EQUIPPED_SOURCE"
    assert rules[0].modifiers[0].property_id == "DamageUpChaosBase"
    assert rules[0].modifiers[0].magnitude_value == 0.10


def test_conditional_suit_rules_keep_base_and_increment_separate():
    rules = BattleEquipmentSuitService.load_rules(
        _StaticDao(),
        (
            _selected("Suit1", 4),
            _selected("Suit3", 4),
            _selected("Suit5", 4),
        ),
        BattleStaticBuffRule,
    )

    values = {
        (row.source_effect_definition_id, row.event_type): (
            row.modifiers[0].property_id,
            row.modifiers[0].magnitude_value,
            row.duration_seconds,
        )
        for row in rules
    }
    assert values[("equipment_suit:Suit1:4", "STATIC_EQUIPPED_SOURCE")] == (
        "DamagePenetrateChaos", 0.12, None,
    )
    assert values[(
        "equipment_suit:Suit1:4",
        "SUIT_SOURCE_REACTION_AFTER|reaction_nova:5,reaction_scorch:0",
    )] == ("DamagePenetrateChaos", 0.12, 20.0)
    assert values[("equipment_suit:Suit3:4", "STATIC_EQUIPPED_SOURCE")] == (
        "DamageUpGeneralBase", 0.18, None,
    )
    assert values[(
        "equipment_suit:Suit5:4",
        "SUIT_TEAM_REACTION_AFTER|reaction_remora:5,reaction_stain:0",
    )] == ("CritBase", 0.14, 20.0)


def test_team_attribute_suits_stack_per_observed_damage_event():
    rules = BattleEquipmentSuitService.load_rules(
        _StaticDao(),
        (_selected("Suit2", 4), _selected("Suit4", 4)),
        BattleStaticBuffRule,
    )

    nature = next(row for row in rules if "Suit2" in row.source_effect_definition_id)
    incantation = next(
        row for row in rules if "Suit4" in row.source_effect_definition_id
    )
    assert (nature.event_type, nature.duration_seconds) == (
        "SUIT_TEAM_ATTRIBUTE_HIT|nature", 10.0,
    )
    assert (nature.modifiers[0].magnitude_value, nature.stack_limit_count) == (
        0.08, 7,
    )
    assert (incantation.event_type, incantation.duration_seconds) == (
        "SUIT_TEAM_ATTRIBUTE_HIT|incantation", 10.0,
    )
    assert (incantation.modifiers[0].magnitude_value, incantation.stack_limit_count) == (
        0.06, 6,
    )


def test_action_suits_use_the_exact_action_boundary():
    rules = BattleEquipmentSuitService.load_rules(
        _StaticDao(),
        tuple(_selected(suit_id, 4) for suit_id in (
            "Suit6", "Suit8", "Suit10", "Suit11", "Suit12",
        )),
        BattleStaticBuffRule,
    )
    by_suit = {row.source_effect_definition_id: row for row in rules}

    assert by_suit["equipment_suit:Suit6:4"].event_type == (
        "ABILITY_EVENT|Q|equipment-suit"
    )
    assert by_suit["equipment_suit:Suit8:4"].event_type == (
        "ABILITY_EVENT_END|E|equipment-suit"
    )
    assert by_suit["equipment_suit:Suit10:4"].stack_count == 10
    assert by_suit["equipment_suit:Suit11:4"].target_scope == "team"
    assert by_suit["equipment_suit:Suit12:4"].event_type == (
        "SUIT_SOURCE_ATTACK_HIT|A"
    )
    assert by_suit["equipment_suit:Suit12:4"].stack_limit_count == 3


def test_non_output_four_piece_effects_remain_visible_without_fake_damage_rule():
    rules = BattleEquipmentSuitService.load_rules(
        _StaticDao(),
        (
            _selected("Suit7", 4, modify_pack_id="Shield_4"),
            _selected("Suit9", 4, modify_pack_id="Heal_4"),
        ),
        BattleStaticBuffRule,
    )

    assert len(rules) == 2
    assert {row.modifiers[0].property_id for row in rules} == {
        "ShieldEfficiency",
        "HealUp",
    }
    assert all(row.event_type == "STATIC_EQUIPPED_SOURCE" for row in rules)


def test_team_attribute_stack_replay_caps_at_seven_live_layers():
    rule = BattleEquipmentSuitService.load_rules(
        _StaticDao(),
        (_selected("Suit2", 4),),
        BattleStaticBuffRule,
    )[0]
    hits = tuple(
        _hit(f"nature:{ordinal}", ordinal * 1_000_000)
        for ordinal in range(1, 9)
    )
    intervals = BattleBuffInferenceService.infer(
        (rule,),
        actions=(),
        hits=hits,
        battle_end_us=20_000_000,
    )

    projection = BattleBuffAttributeProjectionService.project_hit(
        _hit("owner", 9_000_000, character_id=1004),
        intervals,
    )
    assert projection.modifiers[0].property_id == "CritDamageBase"
    assert projection.modifiers[0].additive_value == 0.56


def test_normal_attack_stack_counts_matching_damage_hits():
    rule = BattleEquipmentSuitService.load_rules(
        _StaticDao(),
        (_selected("Suit12", 4),),
        BattleStaticBuffRule,
    )[0]
    hits = tuple(
        replace(
            _hit(
                f"a:{ordinal}",
                ordinal * 1_000_000,
                character_id=1004,
                damage_attribute="psychically",
            ),
            attack_type="普攻",
            ability_id="GA_Lacrimosa_Melee",
        )
        for ordinal in range(1, 5)
    )
    intervals = BattleBuffInferenceService.infer(
        (rule,),
        actions=(),
        hits=hits,
        battle_end_us=10_000_000,
    )
    projection = BattleBuffAttributeProjectionService.project_hit(
        _hit(
            "after-a",
            5_000_000,
            character_id=1004,
            damage_attribute="psychically",
        ),
        intervals,
    )

    assert projection.modifiers[0].additive_value == 0.36
