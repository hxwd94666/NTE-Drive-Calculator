# 覆盖冻结配装到静态 Buff 规则、区间与逐击覆盖的保守推算。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction
from src.services.battle_buff_inference_service import (
    BattleBuffInferenceService,
    BattleStaticBuffRule,
)


class _StaticDao:
    def list_forks(self):
        return [{"fork_id": "fork_test", "star_pack_id": "pack_test"}]

    def list_combat_effect_definitions(self, **_filters):
        return []

    def get_suit(self, _suit_id):
        return None

    def get_equipment_modify_pack(self, _modify_pack_id):
        return None

    def get_equipment_buff_curve(self, _curve_id):
        return None

    def list_character_bound_modifier_effects(self, _character_id):
        return []

    def list_combat_effect_buff_links(self, effect_definition_id):
        if effect_definition_id != "fork_star:pack_test:3":
            return []
        return [{
            "link_kind": "fork_buff",
            "target_asset_path": "/Game/Buff/Buff_Controller",
            "target_available": True,
        }]

    def get_buff_definition(self, asset_path):
        if asset_path == "/Game/Buff/Buff_Controller":
            return {
                "definition_id": "Buff_Controller",
                "duration_policy": None,
                "modifiers": [],
                "triggers": [{
                    "event_type": "EBuffEventType::BUFF_EVENT_E_SKILL_BEGIN",
                    "effect_type": "EBuffEffectType::BUFF_ADD",
                    "target_effect_asset_path": "/Game/Buff/GE_AtkUp",
                    "stack_count": 1,
                    "by_self": True,
                    "target_trigger": False,
                }],
            }
        if asset_path == "/Game/Buff/GE_AtkUp":
            return {
                "definition_id": "GE_AtkUp",
                "duration_policy": "EGameplayEffectDurationType::HasDuration",
                "duration_magnitude": {
                    "ScalableFloatMagnitude": {"Value": 5.0},
                },
                "modifiers": [{
                    "property_id": "AtkUp",
                    "modifier_operation": "EGameplayModOp::Additive",
                    "magnitude_kind": "ScalableFloat",
                    "magnitude_value": 0.2,
                    "calculation_asset_path": None,
                }],
                "triggers": [],
            }
        return None


class _SuitStaticDao(_StaticDao):
    def get_suit(self, suit_id):
        if suit_id != "Suit11":
            return None
        return {
            "suit_id": suit_id,
            "required_shape_ids": [
                "EquipmentGeometry_ZhiJiao1",
                "EquipmentGeometry_ZhiJiao2",
                "EquipmentGeometry_ZhiJiao3",
                "EquipmentGeometry_ZhiJiao4",
            ],
        }

    def list_combat_effect_definitions(self, **filters):
        if filters != {"owner_kind": "equipment_suit", "owner_id": "Suit11"}:
            return []
        return [
            {
                "effect_definition_id": "equipment_suit:Suit11:2",
                "description_zh": "史诗！[2]：充能效率提高12%。",
                "parameters": {
                    "required_count": 2,
                    "modify_pack_id": "GetEfficiency_2",
                },
            },
            {
                "effect_definition_id": "equipment_suit:Suit11:4",
                "description_zh": "传说！[4]：释放极轨终结后，全队角色获得攻击力提升。",
                "parameters": {
                    "required_count": 4,
                    "modify_pack_id": "None",
                },
            },
        ]

    def get_equipment_modify_pack(self, modify_pack_id):
        if modify_pack_id != "GetEfficiency_2":
            return None
        return {
            "modify_pack_id": modify_pack_id,
            "conditions": [],
            "modifiers": [{
                "property_id": "ChargeGetEfficiencyBase",
                "value": 0.12,
                "operation": "MODIFY_MODOP_ADDITIVE",
            }],
        }

    def list_combat_effect_buff_links(self, effect_definition_id):
        if effect_definition_id == "equipment_suit:Suit11:4":
            return [{
                "link_kind": "buff_object",
                "target_asset_path": "/Game/Buff/Buff_Suit11",
                "target_available": True,
            }]
        return super().list_combat_effect_buff_links(effect_definition_id)

    def get_equipment_buff_curve(self, curve_id):
        values = {"Suit11Duration": 20.0, "Suit11Atk": 0.15}
        if curve_id not in values:
            return None
        return {"curve_id": curve_id, "points": [{"value": values[curve_id]}]}

    def get_buff_definition(self, asset_path):
        if asset_path == "/Game/Buff/Buff_Suit11":
            return {
                "definition_id": "Buff_Suit11",
                "duration_policy": "Infinite",
                "modifiers": [],
                "triggers": [{
                    "event_type": "BUFF_EVENT_Q_SKILL_BEGIN",
                    "effect_type": "BUFF_ADD",
                    "target_effect_asset_path": "/Game/Buff/GE_Suit11Atk",
                    "stack_count": 1,
                    "by_self": True,
                    "target_trigger": False,
                }],
            }
        if asset_path == "/Game/Buff/GE_Suit11Atk":
            return {
                "definition_id": "GE_Suit11Atk",
                "duration_policy": "HasDuration",
                "duration_magnitude": {
                    "ScalableFloatMagnitude": {
                        "Curve": {"RowName": "Suit11Duration"},
                        "Value": 1.0,
                    },
                },
                "modifiers": [{
                    "property_id": "AtkUp",
                    "modifier_operation": "Additive",
                    "magnitude_kind": "ScalableFloat",
                    "magnitude_value": 1.0,
                    "magnitude": {
                        "ScalableFloatMagnitude": {
                            "Curve": {"RowName": "Suit11Atk"},
                            "Value": 1.0,
                        },
                    },
                    "calculation_asset_path": None,
                }],
                "triggers": [],
            }
        return super().get_buff_definition(asset_path)


class _SkillStaticDao(_StaticDao):
    def list_character_bound_modifier_effects(self, character_id):
        if character_id != 1036:
            return []
        return [{
            "binding_kind": "active",
            "input_id": "ESkillInputIDType::InputID_GSkill",
            "ability_id": "GA_Zankou_InvisibleSkill",
            "ability_asset_path": "/Game/GA_Zankou_InvisibleSkill",
            "event_tag": "Event.Montage.Player.Display.1",
            "effect_asset_path": "/Game/Buff_Zankou_Invisible",
            "effect_id": "Buff_Zankou_Invisible",
            "target_type_asset_path": "",
        }]

    def get_buff_definition(self, asset_path):
        if asset_path != "/Game/Buff_Zankou_Invisible":
            return super().get_buff_definition(asset_path)
        return {
            "definition_id": "Buff_Zankou_Invisible",
            "duration_policy": "HasDuration",
            "duration_magnitude": {"ScalableFloatMagnitude": {"Value": 5.0}},
            "stack_limit_count": 1,
            "modifiers": [{
                "property_id": "MoveSpeedMaxMult",
                "modifier_operation": "Additive",
                "magnitude_kind": "ScalableFloat",
                "magnitude_value": 0.5,
                "calculation_asset_path": None,
            }],
            "triggers": [],
        }


class _AwakeningDuplicateDao(_StaticDao):
    def list_combat_effect_definitions(self, **filters):
        if filters == {
            "owner_kind": "character_awaken",
            "owner_id": "1036:Effect5",
        }:
            return [{
                "effect_definition_id": "character_awaken:1036:Effect5",
                "parameters": {"modify_pack_id": "ZankouEffect5"},
            }]
        return []

    def get_equipment_modify_pack(self, modify_pack_id):
        if modify_pack_id == "ZankouEffect5":
            return {"modifiers": [{
                "property_id": "ToppleDamageUp",
                "operation": "MODIFY_MODOP_ADDITIVE",
                "value": 3.0,
            }]}
        return None


class _ArachneStaticDao(_StaticDao):
    def list_forks(self):
        return [{
            "fork_id": "fork_Arachne",
            "star_pack_id": "upgradestar_pack_fork_Arachne",
        }]

    def list_combat_effect_definitions(self, **filters):
        if filters != {
            "owner_kind": "fork_star",
            "owner_id": "upgradestar_pack_fork_Arachne",
        }:
            return []
        return [{
            "effect_definition_id": (
                "fork_star:upgradestar_pack_fork_Arachne:1"
            ),
            "description_zh": "生命值提高；释放极轨终结后心灵伤害提高。",
            "parameters": [
                {"name_id": "buff_Arachne_Hp", "value": 0.20},
                {"name_id": "buff_Arachne_Up", "value": 0.10},
                {"name_id": "buff_Arachne_CD", "value": 10.0},
            ],
        }]

    def list_combat_effect_buff_links(self, effect_definition_id):
        if effect_definition_id.endswith("fork_Arachne:1"):
            return [{
                "link_kind": "fork_buff",
                "target_asset_path": "/Game/Buff/Buff_Fork_Arachne_Lv1",
                "target_available": True,
            }]
        return []

    def get_buff_definition(self, asset_path):
        if asset_path == "/Game/Buff/Buff_Fork_Arachne_Lv1":
            return {
                "definition_id": "Buff_Fork_Arachne_Lv1",
                "duration_policy": "Infinite",
                "modifiers": [],
                "triggers": [{
                    "event_type": "BUFF_EVENT_Q_SKILL_BEGIN",
                    "effect_type": "BUFF_ADD",
                    "target_effect_asset_path": "/Game/Buff/Arachne_Effect",
                    "stack_count": 1,
                    "by_self": True,
                    "target_trigger": False,
                }],
            }
        if asset_path == "/Game/Buff/Arachne_Effect":
            return {
                "definition_id": "Buff_Fork_Arachne_Effect",
                "duration_policy": "HasDuration",
                "duration_magnitude": {
                    "ScalableFloatMagnitude": {
                        "Curve": {"RowName": "buff_Arachne_CD"},
                    },
                },
                "stack_limit_count": 1,
                "modifiers": [{
                    "property_id": "DamageUpPsychicallyBase",
                    "modifier_operation": "Additive",
                    "magnitude_kind": "CustomCalculationClass",
                    "magnitude_value": 0.0,
                    "calculation_asset_path": (
                        "/Game/Blueprints/Abilities/Calculation/Fork/"
                        "Fork_Arachne/Cau_Fork_Arachne_UP"
                    ),
                }],
                "triggers": [],
            }
        return None


def _build():
    return {
        "characters": [{
            "character_id": 1072,
            "observed_name": "灵可",
            "fork_id": "fork_test",
            "fork_refinement_level": 3,
            "profile": {"selected_awaken_effect_ids": []},
            "equipment": [],
        }],
    }


def _action():
    return BattleInferredAction(
        action_id="action:e:1",
        character_id=1072,
        character_name="灵可",
        action_name="技能",
        input_kind="E",
        input_sequence="E",
        start_us=1_000_000,
        end_us=2_000_000,
        hits=1,
        damage=1000,
        identity_confidence="中",
        timing_confidence="低",
        inference_basis="fixture",
        evidence_event_ids=("1:primary",),
        gameplay_effect_ids=("GE_Test",),
    )


def _hit(time_us):
    return BattleAnalysisHit(
        event_id=f"{time_us}:primary",
        sequence=time_us,
        relative_time_us=time_us,
        character_id=1072,
        character_name="灵可",
        skill_name="技能",
        damage_name="伤害",
        damage_component="skill",
        attack_type="skill",
        damage_attribute="nature",
        target_id="target",
        target_name="目标",
        damage=1000,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
    )


class BattleBuffInferenceServiceTests(unittest.TestCase):
    def test_confirmed_team_awaken_rule_replaces_generic_self_duplicate(self) -> None:
        build = {"characters": [{
            "character_id": 1036,
            "observed_name": "残虹",
            "profile": {
                "selected_awaken_effect_ids": ["Effect5"],
                "awakening_selection_initialized": True,
            },
            "equipment": [],
        }]}

        rules = BattleBuffInferenceService.load_rules(
            _AwakeningDuplicateDao(),
            build,
        )
        effect_rules = tuple(
            row for row in rules
            if row.source_effect_definition_id == "character_awaken:1036:Effect5"
        )

        self.assertEqual(1, len(effect_rules))
        self.assertEqual("team", effect_rules[0].target_scope)
        self.assertEqual(3.0, effect_rules[0].modifiers[0].magnitude_value)

    def test_after_damage_buff_starts_after_the_triggering_hit(self) -> None:
        rule = BattleStaticBuffRule(
            rule_id="after-damage",
            source_effect_definition_id="character_awaken:1036:resonance_6",
            source_kind="test",
            source_character_id=1072,
            source_character_name="灵可",
            source_asset_path="/Game/Test/Source",
            target_asset_path="/Game/Test/Attack",
            target_name="伤害后攻击提升",
            target_scope="self",
            event_type="EBuffEventType::BUFF_EVENT_SKILL_AFTER_DAMAGE",
            effect_type="ADD",
            duration_policy="HasDuration",
            duration_seconds=20.0,
            stack_count=1,
            modifiers=(),
        )
        hit = _hit(1_000_000)

        intervals = BattleBuffInferenceService.infer(
            (rule,),
            actions=(),
            hits=(hit,),
            battle_end_us=30_000_000,
        )

        self.assertEqual(1_000_001, intervals[0].start_us)
        self.assertEqual((hit.event_id,), intervals[0].evidence_event_ids)

    def test_trigger_application_requirement_limits_mitsuki_finish_to_e(self) -> None:
        rule = BattleStaticBuffRule(
            rule_id="mitsuki-e-finish",
            source_effect_definition_id="character_awaken:1070:Effect6",
            source_kind="test",
            source_character_id=1072,
            source_character_name="灵可",
            source_asset_path="/Game/Test/Source",
            target_asset_path="/Game/Test/Target",
            target_name="E 结束触发",
            target_scope="self",
            event_type="EBuffEventType::BUFF_EVENT_SKILL_REALFINISH",
            effect_type="ADD",
            duration_policy="HasDuration",
            duration_seconds=5.0,
            stack_count=1,
            modifiers=(),
            application_requirement_asset_path=(
                "/Game/Blueprints/Abilities/Condition/Player/Mitsuki/"
                "Con_Mitsuki_Lv6"
            ),
        )
        e_action = _action()
        q_action = replace(
            e_action,
            action_id="action:q:1",
            input_kind="Q",
            input_sequence="Q",
        )

        intervals = BattleBuffInferenceService.infer(
            (rule,),
            actions=(e_action, q_action),
            hits=(),
            battle_end_us=30_000_000,
        )

        self.assertEqual(1, len(intervals))
        self.assertEqual((e_action.action_id,), intervals[0].evidence_action_ids)

    def test_shinku_q_debuff_starts_after_hit_and_keeps_target_identity(self) -> None:
        rule = BattleStaticBuffRule(
            rule_id="shinku-q-target",
            source_effect_definition_id="character_awaken:1076:Effect2",
            source_kind="test",
            source_character_id=1072,
            source_character_name="灵可",
            source_asset_path="/Game/Test/Source",
            target_asset_path="/Game/Test/LightResist",
            target_name="Q 命中后减抗",
            target_scope="target",
            event_type="EBuffEventType::BUFF_EVENT_SKILL_AFTER_HIT",
            effect_type="ADD",
            duration_policy="HasDuration",
            duration_seconds=20.0,
            stack_count=1,
            modifiers=(),
            application_requirement_asset_path=(
                "/Game/Condition/Con_Shinku_CurIsUltraDamage"
            ),
        )
        ordinary = replace(
            _hit(1_000_000),
            target_id="target-a",
            ability_id="GA_Shinku_Melee",
        )
        ultra = replace(
            _hit(2_000_000),
            target_id="target-b",
            attack_type="q技能",
            ability_id="GA_Shinku_UltraSkill",
        )

        intervals = BattleBuffInferenceService.infer(
            (rule,),
            actions=(),
            hits=(ordinary, ultra),
            battle_end_us=30_000_000,
        )

        self.assertEqual(1, len(intervals))
        self.assertEqual(2_000_001, intervals[0].start_us)
        self.assertEqual("target-b", intervals[0].target_id)
        self.assertEqual((ultra.event_id,), intervals[0].evidence_event_ids)

    def test_unknown_formal_trigger_condition_is_not_assumed_true(self) -> None:
        rule = BattleStaticBuffRule(
            rule_id="unknown-condition",
            source_effect_definition_id="character_awaken:test",
            source_kind="test",
            source_character_id=1072,
            source_character_name="灵可",
            source_asset_path="/Game/Test/Source",
            target_asset_path="/Game/Test/Target",
            target_name="未知条件",
            target_scope="self",
            event_type="EBuffEventType::BUFF_EVENT_SKILL_AFTER_HIT",
            effect_type="ADD",
            duration_policy="HasDuration",
            duration_seconds=5.0,
            stack_count=1,
            modifiers=(),
            application_requirement_asset_path="/Game/Condition/Con_Unknown",
        )

        intervals = BattleBuffInferenceService.infer(
            (rule,),
            actions=(),
            hits=(_hit(1_000_000),),
            battle_end_us=10_000_000,
        )

        self.assertEqual((), intervals)

    def test_zero_first_awakening_creates_hit_specific_defense_ignore_rule(self):
        build = {
            "characters": [{
                "character_id": 1051,
                "observed_name": "零",
                "profile": {
                    "selected_awaken_effect_ids": ["Effect1", "Effect6"],
                    "awakening_selection_initialized": True,
                },
                "equipment": [],
            }],
        }

        rules = BattleBuffInferenceService.load_rules(_StaticDao(), build)
        first_gaze = next(
            row for row in rules
            if row.source_effect_definition_id == "character_awaken:1051:Effect1"
        )

        self.assertEqual("STATIC_EQUIPPED_SOURCE", first_gaze.event_type)
        self.assertEqual("self", first_gaze.target_scope)
        self.assertEqual("DefIgnore", first_gaze.modifiers[0].property_id)
        self.assertEqual(0.75, first_gaze.modifiers[0].magnitude_value)
        self.assertEqual(
            "battle-awakening:zero-first-gaze-extra-hit",
            first_gaze.modifiers[0].application_requirement_asset_path,
        )

    def test_known_fork_calculation_keeps_path_and_resolves_refinement_value(self):
        build = {
            "characters": [{
                "character_id": 1039,
                "observed_name": "法帝娅",
                "fork_id": "fork_Arachne",
                "fork_refinement_level": 1,
                "profile": {"selected_awaken_effect_ids": []},
                "equipment": [],
            }],
        }
        action = BattleInferredAction(
            action_id="action:q:arachne",
            character_id=1039,
            character_name="法帝娅",
            action_name="极轨终结",
            input_kind="Q",
            input_sequence="Q",
            start_us=2_000_000,
            end_us=3_000_000,
            hits=1,
            damage=100,
            identity_confidence="中",
            timing_confidence="低",
            inference_basis="fixture",
            evidence_event_ids=("2:primary",),
            gameplay_effect_ids=("GE_Q",),
        )

        rules = BattleBuffInferenceService.load_rules(
            _ArachneStaticDao(),
            build,
        )
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(action,),
            hits=(),
            battle_end_us=20_000_000,
        )
        interval = next(row for row in intervals if row.start_us == 2_000_000)

        self.assertEqual(12_000_000, interval.end_us)
        self.assertEqual(0.10, interval.modifiers[0].magnitude_value)
        self.assertEqual("confirmed-fork:arachne-q-window", interval.buff_asset_path)
        self.assertFalse(interval.modifiers[0].calculation_asset_path)
        self.assertEqual("高", interval.modifiers[0].value_confidence)

    def test_direct_skill_buff_uses_bound_input_action(self):
        build = {"characters": [{
            "character_id": 1036,
            "observed_name": "残虹",
            "profile": {"selected_awaken_effect_ids": []},
            "equipment": [],
        }]}
        action = BattleInferredAction(
            action_id="action:g:1",
            character_id=1036,
            character_name="残虹",
            action_name="隐匿技能",
            input_kind="G",
            input_sequence="G",
            start_us=4_000_000,
            end_us=4_500_000,
            hits=1,
            damage=100,
            identity_confidence="中",
            timing_confidence="低",
            inference_basis="fixture",
            evidence_event_ids=("4:primary",),
            gameplay_effect_ids=("GE_G",),
        )

        rules = BattleBuffInferenceService.load_rules(_SkillStaticDao(), build)
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(action,),
            hits=(),
            battle_end_us=12_000_000,
        )

        self.assertEqual(1, len(rules))
        self.assertEqual((4_000_000, 9_000_000), (
            intervals[0].start_us,
            intervals[0].end_us,
        ))
        self.assertEqual("MoveSpeedMaxMult", intervals[0].modifiers[0].property_id)

    def test_suit_condition_counts_modules_matching_core_required_shapes(self):
        build = {
            "characters": [{
                "character_id": 1003,
                "observed_name": "早雾",
                "profile": {"selected_awaken_effect_ids": []},
                "equipment": [
                    {"kind": "core", "suit_id": "Suit11", "geometry": "Core"},
                    {"kind": "module", "suit_id": "", "geometry": "ZhiJiao1"},
                    {"kind": "module", "suit_id": "", "geometry": "ZhiJiao1"},
                    {"kind": "module", "suit_id": "", "geometry": "Hen3"},
                ],
            }],
        }

        rules = BattleBuffInferenceService.load_rules(_SuitStaticDao(), build)

        self.assertEqual(1, len(rules))
        self.assertEqual(
            "equipment_suit:Suit11:2",
            rules[0].source_effect_definition_id,
        )
        self.assertEqual("STATIC_EQUIPPED_SOURCE", rules[0].event_type)
        self.assertEqual(
            "ChargeGetEfficiencyBase",
            rules[0].modifiers[0].property_id,
        )
        self.assertEqual(0.12, rules[0].modifiers[0].magnitude_value)

    def test_suit_curve_resolves_duration_value_and_team_scope(self):
        modules = [
            {"kind": "module", "suit_id": "", "geometry": shape}
            for shape in ("ZhiJiao1", "ZhiJiao2", "ZhiJiao3", "ZhiJiao4")
        ]
        build = {
            "characters": [{
                "character_id": 1003,
                "observed_name": "早雾",
                "profile": {"selected_awaken_effect_ids": []},
                "equipment": [
                    {"kind": "core", "suit_id": "Suit11", "geometry": "Core"},
                    *modules,
                ],
            }],
        }
        rules = BattleBuffInferenceService.load_rules(_SuitStaticDao(), build)
        action = BattleInferredAction(
            action_id="action:q:1",
            character_id=1003,
            character_name="早雾",
            action_name="极轨终结",
            input_kind="Q",
            input_sequence="Q",
            start_us=2_000_000,
            end_us=3_000_000,
            hits=1,
            damage=1000,
            identity_confidence="中",
            timing_confidence="低",
            inference_basis="fixture",
            evidence_event_ids=("2:primary",),
            gameplay_effect_ids=("GE_Q",),
        )

        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(action,),
            hits=(),
            battle_end_us=30_000_000,
        )

        team_interval = next(
            row for row in intervals
            if row.source_effect_definition_id == "equipment_suit:Suit11:4"
        )
        self.assertEqual((2_000_000, 22_000_000), (
            team_interval.start_us,
            team_interval.end_us,
        ))
        self.assertEqual("team", team_interval.target_scope)
        self.assertEqual(0.15, team_interval.modifiers[0].magnitude_value)

    def test_exact_fork_refinement_generates_timed_self_buff(self):
        rules = BattleBuffInferenceService.load_rules(_StaticDao(), _build())
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(_action(),),
            hits=(_hit(1_000_000),),
            battle_end_us=10_000_000,
        )

        self.assertEqual(1, len(rules))
        self.assertEqual(1, len(intervals))
        interval = intervals[0]
        self.assertEqual((1_000_000, 6_000_000), (interval.start_us, interval.end_us))
        self.assertEqual("self", interval.target_scope)
        self.assertEqual("AtkUp", interval.modifiers[0].property_id)
        self.assertEqual(0.2, interval.modifiers[0].magnitude_value)
        self.assertEqual(
            (interval,),
            BattleBuffInferenceService.active_for_hit(
                intervals,
                _hit(3_000_000),
            ),
        )
        self.assertEqual(
            (),
            BattleBuffInferenceService.active_for_hit(
                intervals,
                _hit(7_000_000),
            ),
        )

    def test_generic_buff_duration_does_not_advance_during_time_stop(self):
        rules = BattleBuffInferenceService.load_rules(_StaticDao(), _build())
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(_action(),),
            hits=(_hit(1_000_000),),
            battle_end_us=20_000_000,
            time_stop_intervals=((2_000_000, 10_000_000),),
        )

        self.assertEqual((1_000_000, 14_000_000), (
            intervals[0].start_us,
            intervals[0].end_us,
        ))
        self.assertEqual(
            (intervals[0],),
            BattleBuffInferenceService.active_for_hit(
                intervals,
                _hit(13_999_999),
            ),
        )
        self.assertEqual(
            (),
            BattleBuffInferenceService.active_for_hit(
                intervals,
                _hit(14_000_000),
            ),
        )


if __name__ == "__main__":
    unittest.main()
