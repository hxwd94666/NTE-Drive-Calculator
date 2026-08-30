# 角色培养被动必须按突破解锁，并以显式规则进入固定轴逐击重放。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService,
    passive_requirement_applies,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_buff_inference_service import (
    BattleBuffInferenceService,
    BattleStaticBuffRule,
)


def _character(character_id: int, stage: int, name: str = "角色"):
    return {
        "character_id": character_id,
        "observed_name": name,
        "breakthrough_stage": stage,
        "profile": {},
    }


def _hit(*, character_id=1046, ability_id="", gameplay_effect_id=""):
    return BattleAnalysisHit(
        event_id="1:primary",
        sequence=1,
        relative_time_us=1_000_000,
        character_id=character_id,
        character_name="角色",
        skill_name="技能",
        damage_name="伤害",
        damage_component="skill",
        attack_type="skill",
        damage_attribute="cosmos",
        target_id="target",
        target_name="目标",
        damage=1000,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id=ability_id,
        gameplay_effect_id=gameplay_effect_id,
    )


def _action(input_kind: str, start_us: int) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=f"action:{input_kind}:{start_us}",
        character_id=1075,
        character_name="伊洛伊",
        action_name=input_kind,
        input_kind=input_kind,
        input_sequence=input_kind,
        start_us=start_us,
        end_us=start_us + 500_000,
        hits=1,
        damage=100.0,
        identity_confidence="中",
        timing_confidence="低",
        inference_basis="fixture",
        evidence_event_ids=(f"{start_us}:primary",),
        gameplay_effect_ids=(f"GE_Oneiroi_{input_kind}",),
    )


class BattleCharacterPassiveServiceTests(unittest.TestCase):
    def test_catalog_covers_two_passives_for_every_logical_character(self):
        catalog = BattleCharacterPassiveService.catalog()

        self.assertEqual(44, len(catalog))
        self.assertEqual(44, len({row.passive_id for row in catalog}))
        self.assertEqual(
            {2, 4},
            {row.unlock_stage for row in catalog},
        )
        by_character = {}
        for row in catalog:
            by_character.setdefault(row.character_id, []).append(row)
            self.assertTrue(row.replay_kind)
            self.assertTrue(row.adapter_id)
            self.assertTrue(row.fixed_axis_policy)
        self.assertEqual(22, len(by_character))
        self.assertTrue(all(len(rows) == 2 for rows in by_character.values()))

    def test_enabled_passives_follow_breakthrough_and_merge_protagonist_forms(self):
        build = {
            "characters": [
                _character(1003, 1, "早雾"),
                _character(1023, 2, "白藏"),
                _character(1051, 4, "零"),
            ]
        }

        enabled = BattleCharacterPassiveService.enabled_passives(build)

        self.assertEqual(
            {
                "PASSIVE-1023-GA_Cang_Passive_1",
                "PASSIVE-1046-GA_Female_Passive_1",
                "PASSIVE-1046-GA_Female_Passive_2",
            },
            {row.definition.passive_id for row in enabled},
        )
        protagonist = next(
            row for row in enabled
            if row.definition.passive_id.endswith("GA_Female_Passive_2")
        )
        self.assertEqual(1051, protagonist.source_character_id)

    def test_direct_rule_specs_only_include_safe_formula_modifiers(self):
        build = {
            "characters": [
                _character(1023, 4, "白藏"),
                _character(1039, 4, "法帝娅"),
                _character(1046, 4, "零"),
            ]
        }

        specs = BattleCharacterPassiveService.rule_specs(build)

        values = {
            (row.passive_id, modifier.property_id): modifier.magnitude_value
            for row in specs
            for modifier in row.modifiers
        }
        self.assertEqual(0.20, values[("PASSIVE-1023-GA_Cang_Passive_2", "AtkUp")])
        self.assertEqual(0.10, values[("PASSIVE-1039-GA_Fadia_Passive_2", "HPMaxUp")])
        self.assertEqual(
            0.25,
            values[("PASSIVE-1046-GA_Female_Passive_2", "DamageUpGeneralBase")],
        )

    def test_oneiroi_healing_passive_is_not_materialized_from_actions(self):
        specs = BattleCharacterPassiveService.rule_specs({
            "characters": [_character(1075, 4, "伊洛伊")],
        })

        self.assertFalse(any(
            row.passive_id == "PASSIVE-1075-GA_Oneiroi_Passive_2"
            for row in specs
        ))

    def test_skill_requirement_is_exactly_scoped_to_named_damage_family(self):
        requirement = (
            "battle-passive|ability-prefix-any="
            "GA_Female046_UltraSkill,GA_Female051_UltraSkill"
        )

        self.assertTrue(passive_requirement_applies(
            requirement,
            _hit(ability_id="GA_Female046_UltraSkill"),
        )[0])
        self.assertTrue(passive_requirement_applies(
            requirement,
            _hit(character_id=1051, ability_id="GA_Female051_UltraSkill_Fantasy"),
        )[0])
        self.assertFalse(passive_requirement_applies(
            requirement,
            _hit(ability_id="GA_Female046_Skill"),
        )[0])

    def test_jin_terminal_segment_gets_multiplier_adjustment_only_when_unlocked(self):
        locked = _character(1052, 3, "浔")
        unlocked = _character(1052, 4, "浔")

        self.assertEqual(
            (1.0, ""),
            BattleCharacterPassiveService.skill_multiplier_adjustment(
                locked,
                damage_id="GE_Player_Jin_UltraSkill3_Damage",
                ability_id="GA_Jin_UltraSkill",
            ),
        )
        multiplier, basis = BattleCharacterPassiveService.skill_multiplier_adjustment(
            unlocked,
            damage_id="GE_Player_Jin_UltraSkill3_Damage",
            ability_id="GA_Jin_UltraSkill",
        )
        self.assertEqual(2.0, multiplier)
        self.assertIn("天下万宝", basis)
        self.assertEqual(
            (1.0, ""),
            BattleCharacterPassiveService.skill_multiplier_adjustment(
                unlocked,
                damage_id="GE_Player_Jin_UltraSkill2_Damage",
                ability_id="GA_Jin_UltraSkill",
            ),
        )

    def test_haniel_dark_star_drain_waits_for_target_state_end_evidence(self):
        character = _character(1020, 2, "哈尼娅")
        character["stats"] = [{
            "source_group": "resolved",
            "property_id": "AtkBase",
            "value": 1_250,
        }]

        specs = BattleCharacterPassiveService.rule_specs({
            "characters": [character]
        })

        self.assertFalse(any(
            row.passive_id == "PASSIVE-1020-GA_Haniel_Passive_1"
            for row in specs
        ))

    def test_static_passive_projects_only_to_its_owner(self):
        build = {"characters": [_character(1023, 4, "白藏")]}
        rules = BattleCharacterPassiveService.load_rules(
            build,
            BattleStaticBuffRule,
        )
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(),
            hits=(),
            battle_end_us=5_000_000,
        )

        owner = BattleBuffAttributeProjectionService.project_hit(
            _hit(character_id=1023),
            intervals,
        )
        teammate = BattleBuffAttributeProjectionService.project_hit(
            _hit(character_id=1003),
            intervals,
        )

        self.assertEqual(
            0.20,
            next(row for row in owner.modifiers if row.property_id == "AtkUp").additive_value,
        )
        self.assertEqual((), teammate.modifiers)

    def test_protagonist_q_modifier_does_not_leak_to_e(self):
        build = {"characters": [_character(1046, 4, "零")]}
        intervals = BattleBuffInferenceService.infer(
            BattleCharacterPassiveService.load_rules(build, BattleStaticBuffRule),
            actions=(),
            hits=(),
            battle_end_us=5_000_000,
        )

        q_projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(ability_id="GA_Female046_UltraSkill"),
            intervals,
        )
        e_projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(ability_id="GA_Female046_Skill"),
            intervals,
        )

        self.assertEqual(
            0.25,
            next(
                row for row in q_projection.modifiers
                if row.property_id == "DamageUpGeneralBase"
            ).additive_value,
        )
        self.assertEqual((), e_projection.modifiers)

    def test_mitsuki_stack_refreshes_the_whole_five_second_group(self):
        build = {"characters": [_character(1070, 4, "海月")]}
        first = replace(
            _hit(character_id=1070),
            event_id="1:primary",
            relative_time_us=1_000_000,
            gameplay_effect_id="GE_Player_Mitsuki_PerfectAtkBullet_Damage",
        )
        second = replace(
            first,
            event_id="2:primary",
            relative_time_us=5_000_000,
        )
        probe = replace(
            first,
            event_id="3:primary",
            relative_time_us=9_000_000,
            skill_name="普通攻击",
        )
        intervals = BattleBuffInferenceService.infer(
            BattleCharacterPassiveService.load_rules(build, BattleStaticBuffRule),
            actions=(),
            hits=(first, second),
            battle_end_us=12_000_000,
        )

        projection = BattleBuffAttributeProjectionService.project_hit(
            probe,
            intervals,
        )

        self.assertEqual(
            0.02,
            next(row for row in projection.modifiers if row.property_id == "AtkUp").additive_value,
        )

    def test_mitsuki_effect_six_raises_the_shared_gradual_stack_cap(self):
        character = _character(1070, 4, "海月")
        character["profile"] = {
            "awakening_selection_initialized": True,
            "selected_awaken_effect_ids": ["Effect6"],
        }

        rules = BattleCharacterPassiveService.load_rules(
            {"characters": [character]},
            BattleStaticBuffRule,
        )

        gradual = next(
            row for row in rules
            if row.source_effect_definition_id.endswith("GA_Mitsuki_Passive2")
        )
        self.assertEqual(20, gradual.stack_limit_count)

    def test_shinku_reaction_stack_obeys_one_second_active_time_cooldown(self):
        build = {"characters": [_character(1076, 2, "真红")]}
        trigger = replace(
            _hit(character_id=1076),
            event_id="1:primary",
            relative_time_us=1_000_000,
            gameplay_effect_id="GE_Player_Shinku_ReactionAOE_Damage",
        )
        same_trigger_second_target = replace(
            trigger,
            event_id="2:primary",
            target_id="target-2",
        )
        during_cooldown = replace(
            trigger,
            event_id="3:primary",
            relative_time_us=1_800_000,
        )
        after_cooldown = replace(
            trigger,
            event_id="4:primary",
            relative_time_us=2_000_000,
        )
        probe = replace(
            trigger,
            event_id="5:primary",
            relative_time_us=2_500_000,
            gameplay_effect_id="GE_Player_Shinku_Melee1_Damage",
        )

        intervals = BattleBuffInferenceService.infer(
            BattleCharacterPassiveService.load_rules(build, BattleStaticBuffRule),
            actions=(),
            hits=(
                trigger,
                same_trigger_second_target,
                during_cooldown,
                after_cooldown,
            ),
            battle_end_us=4_000_000,
        )
        projection = BattleBuffAttributeProjectionService.project_hit(
            probe,
            intervals,
        )

        self.assertEqual(
            0.10,
            next(
                row for row in projection.modifiers
                if row.property_id == "AtkUp"
            ).additive_value,
        )


if __name__ == "__main__":
    unittest.main()
