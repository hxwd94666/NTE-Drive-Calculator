# 验证第四批人工确认弧盘的周期、随机、标记与召唤边界。
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_buff_inference_service import (
    BattleBuffInferenceService,
    BattleStaticBuffRule,
)
from src.services.battle_fork_periodic_refinement_service import (
    BattleForkPeriodicRefinementService,
)
from src.services.battle_fork_refinement_service import BattleForkRefinementService


def _selected(owner_id: str, parameters: dict[str, float]) -> SimpleNamespace:
    return SimpleNamespace(
        effect_definition_id=f"fork_star:{owner_id}:1",
        character_id=1001,
        character_name="弧盘装备者",
        definition={"parameters": parameters},
    )


def _rules(owner_id: str, parameters: dict[str, float]):
    return BattleForkRefinementService.rules_for_selected_effect(
        _selected(owner_id, parameters),
        BattleStaticBuffRule,
    )


def _action(
    ordinal: int,
    character_id: int,
    input_kind: str,
    start_us: int,
    end_us: int,
) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=f"action:{ordinal}",
        character_id=character_id,
        character_name=f"角色{character_id}",
        action_name=input_kind,
        input_kind=input_kind,
        input_sequence=input_kind,
        start_us=start_us,
        end_us=end_us,
        hits=1,
        damage=1000.0,
        identity_confidence="中",
        timing_confidence="中",
        inference_basis="test",
        evidence_event_ids=(f"event:{ordinal}",),
        gameplay_effect_ids=(f"GE_Test_{input_kind}",),
    )


def _hit(
    ordinal: int,
    time_us: int,
    *,
    input_kind: str,
    damage_attribute: str = "nature",
    target_id: str = "target",
) -> BattleAnalysisHit:
    attack_types = {"A": "普攻", "E": "E技能", "Q": "Q技能"}
    return BattleAnalysisHit(
        event_id=f"hit:{ordinal}",
        sequence=ordinal,
        relative_time_us=time_us,
        character_id=1001,
        character_name="弧盘装备者",
        skill_name=f"测试{input_kind}",
        damage_name="测试伤害",
        damage_component="skill",
        attack_type=attack_types[input_kind],
        damage_attribute=damage_attribute,
        target_id=target_id,
        target_name=target_id,
        damage=1000.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id=(
            "GA_Test_UltraSkill" if input_kind == "Q"
            else "GA_Test_Skill" if input_kind == "E"
            else "GA_Test_Melee"
        ),
        gameplay_effect_id=f"GE_Test_{input_kind}_Damage",
    )


def _property(projection, property_id: str) -> float | None:
    return next(
        (
            row.additive_value for row in projection.modifiers
            if row.property_id == property_id
        ),
        None,
    )


class BattleForkPeriodicRefinementServiceTests(unittest.TestCase):
    def test_lunar_phase_q_begins_immediately_and_refreshes(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_LunarPhase",
            {
                "buff_LunarPhase_Atk": 0.20,
                "buff_LunarPhase_Up": 0.32,
                "buff_LunarPhase_DefIgnore": 0.12,
                "buff_LunarPhase_CD": 20.0,
            },
        )
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(
                _action(1, 1001, "Q", 2_000_000, 3_000_000),
                _action(2, 1001, "Q", 10_000_000, 11_000_000),
            ),
            hits=(),
            battle_end_us=40_000_000,
        )
        projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(1, 2_000_000, input_kind="Q", damage_attribute="cosmos"),
            intervals,
        )
        window = next(row for row in intervals if "Q 开始" in row.buff_name)

        self.assertEqual((2_000_000, 30_000_000), (
            window.start_us,
            window.end_us,
        ))
        self.assertAlmostEqual(0.20, _property(projection, "AtkUp"))
        self.assertAlmostEqual(
            0.32,
            _property(projection, "DamageUpCosmosBase"),
        )
        self.assertAlmostEqual(0.12, _property(projection, "DefIgnore"))

    def test_motor_candy_uses_raw_time_and_resets_on_each_exit(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_MotorCandy",
            {"buff_MotorCandy_CD": 1.0, "buff_MotorCandy_AtkUp": 0.05},
        )
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(
                _action(1, 1001, "A", 0, 200_000),
                _action(2, 2002, "A", 6_000_000, 6_200_000),
                _action(3, 1001, "E", 8_000_000, 8_500_000),
            ),
            hits=(),
            battle_end_us=12_000_000,
            time_stop_intervals=((2_000_000, 5_000_000),),
        )
        stacks = tuple(
            (row.start_us, row.end_us, row.stacks)
            for row in intervals
        )

        self.assertEqual((
            (1_000_000, 2_000_000, 1),
            (2_000_000, 3_000_000, 2),
            (3_000_000, 4_000_000, 3),
            (4_000_000, 5_000_000, 4),
            (5_000_000, 6_000_000, 5),
            (9_000_000, 10_000_000, 1),
            (10_000_000, 11_000_000, 2),
            (11_000_000, 12_000_000, 3),
        ), stacks)
        self.assertTrue(all("时停不暂停" in row.inference_basis for row in intervals))

    def test_nakupeda_records_one_equal_random_outcome_without_fabricating_it(self) -> None:
        selected = _selected(
            "upgradestar_pack_fork_Nakupeda",
            {
                "buff_Nakipeda_Hp": 0.24,
                "buff_Nakipeda_effect1": 0.20,
                "buff_Nakipeda_effect2": 0.20,
                "buff_Nakipeda_effect2CD": 15.0,
                "buff_Nakipeda_effect3": 0.10,
                "buff_Nakipeda_CD": 30.0,
            },
        )
        rules = BattleForkRefinementService.rules_for_selected_effect(
            selected,
            BattleStaticBuffRule,
        )
        semantics = BattleForkPeriodicRefinementService.nakupeda_random_semantics(
            selected
        )

        self.assertEqual(1, len(rules))
        self.assertEqual("HPMaxUp", rules[0].modifiers[0].property_id)
        self.assertIsNotNone(semantics)
        assert semantics is not None
        self.assertEqual(1.0 / 3.0, semantics.outcome_probability)
        self.assertTrue(semantics.applies_exactly_one_outcome)
        self.assertEqual("one_runtime_selected", semantics.lowest_hp_tie_resolution)

    def test_nest_bird_keeps_enemy_mark_out_of_player_damage_projection(self) -> None:
        rules = _rules("upgradestar_pack_fork_NestBird", {})
        semantics = BattleForkPeriodicRefinementService.nest_bird_mark_semantics()
        hits = (
            _hit(1, 1_000_000, input_kind="Q", target_id="enemy:a"),
            _hit(2, 1_000_000, input_kind="Q", target_id="enemy:b"),
            _hit(3, 5_000_000, input_kind="Q", target_id="enemy:a"),
        )
        actions = (
            replace(
                _action(1, 1001, "Q", 900_000, 1_500_000),
                evidence_event_ids=("hit:1", "hit:2"),
            ),
            replace(
                _action(2, 1001, "Q", 4_900_000, 5_500_000),
                evidence_event_ids=("hit:3",),
            ),
        )
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=actions,
            hits=hits,
            battle_end_us=30_000_000,
        )

        self.assertEqual("unknown", rules[0].target_scope)
        self.assertEqual(-0.18, rules[0].modifiers[0].magnitude_value)
        self.assertEqual({
            ("fork-target:enemy:a", 1_000_001, 25_000_001),
            ("fork-target:enemy:b", 1_000_001, 21_000_001),
        }, {
            (row.target_scope, row.start_us, row.end_us) for row in intervals
        })
        self.assertTrue(semantics.applies_after_triggering_hit)
        self.assertTrue(semantics.refreshes_same_target)
        self.assertTrue(semantics.tracks_targets_independently)
        self.assertTrue(semantics.requires_observed_q_hit_target)

    def test_paper_plane_applies_once_to_e_or_q_nature_damage_only(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_PaperPlane",
            {"buff_PaperPlane_Up": 0.20},
        )
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(),
            hits=(),
            battle_end_us=10_000_000,
        )
        e_projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(1, 1_000_000, input_kind="E"),
            intervals,
        )
        q_projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(2, 1_000_000, input_kind="Q"),
            intervals,
        )
        normal_projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(3, 1_000_000, input_kind="A"),
            intervals,
        )
        wrong_element = BattleBuffAttributeProjectionService.project_hit(
            _hit(4, 1_000_000, input_kind="E", damage_attribute="cosmos"),
            intervals,
        )

        self.assertAlmostEqual(
            0.20,
            _property(e_projection, "DamageUpNatureBase"),
        )
        self.assertAlmostEqual(
            0.20,
            _property(q_projection, "DamageUpNatureBase"),
        )
        self.assertIsNone(_property(normal_projection, "DamageUpNatureBase"))
        self.assertIsNone(_property(wrong_element, "DamageUpNatureBase"))

    def test_police_rat_uses_only_observed_hits_with_low_confidence_assumptions(self) -> None:
        selected = _selected(
            "upgradestar_pack_fork_PoliceRat",
            {
                "buff_PoliceRat_AtkUp": 0.15,
                "buff_PoliceRat_Up": 0.15,
                "buff_PoliceRat_SkillDamage": 1.0,
                "buff_Rat_CD": 60.0,
            },
        )
        rules = BattleForkRefinementService.rules_for_selected_effect(
            selected,
            BattleStaticBuffRule,
        )
        semantics = BattleForkPeriodicRefinementService.police_rat_semantics(
            selected
        )

        self.assertEqual("AtkUp", rules[0].modifiers[0].property_id)
        self.assertEqual(2, len(rules))
        self.assertEqual("unknown", rules[1].target_scope)
        self.assertEqual(
            "DamageUpGeneralBase",
            rules[1].modifiers[0].property_id,
        )
        self.assertIsNotNone(semantics)
        assert semantics is not None
        self.assertTrue(semantics.derived_hit_can_crit)
        self.assertTrue(semantics.inherits_owner_damage_bonuses)
        self.assertTrue(semantics.uses_observed_axis_hits_only)
        self.assertEqual("低", semantics.confidence)
        self.assertEqual(1.0, semantics.attack_coefficient)
        self.assertEqual(0.15, semantics.boss_damage_bonus)


if __name__ == "__main__":
    unittest.main()
