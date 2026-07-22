# 覆盖项目直伤金标准的属性区、乘区和场景差异。
"""Tests for the project-standard direct-damage calculation service."""

from __future__ import annotations

import unittest

from src.services.damage_calculation_service import (
    DamageCalculationService,
    DamageScalingStat,
    DamageScene,
    DarkStarInstances,
    DirectDamageInput,
    DotDamageInput,
    ToppleDamageInput,
    RingCharacter,
    calculate_ring_amplification,
    calculate_ring_strength_multiplier,
    calculate_dissonance_topple_reduction,
    calculate_weave_followup_damage,
    effective_skill_level,
    reaction_multiplier_for_character_level,
    reaction_tier_for_character_level,
    select_ring_owner,
    skill_tier_for_effective_level,
)


def standard_values(**updates) -> DirectDamageInput:
    values = {
        "skill_multiplier": 2.0,
        "scaling_stat": DamageScalingStat.ATTACK,
        "attack_base": 150.0,
        "attack_up": 0.20,
        "attack_add": 10.0,
        "health_base": 1_000.0,
        "health_up": 0.10,
        "health_add": 20.0,
        "defense_base": 200.0,
        "defense_up": 0.50,
        "defense_add": 10.0,
        "character_level": 100.0,
        "enemy_level": 100.0,
        "crit_rate": 0.50,
        "crit_damage": 0.50,
        "defense_penetration": 0.20,
        "defense_reduction": 0.25,
        "damage_increases": (0.30, 0.20),
        "vulnerability_increases": (0.20,),
        "enemy_resistance_reductions": (0.10,),
        "resistance_penetrations": (0.15,),
        "independent_damage_bonuses": (0.20, 0.25),
    }
    values.update(updates)
    return DirectDamageInput(**values)


class DamageCalculationServiceTests(unittest.TestCase):
    def test_direct_damage_returns_every_confirmed_multiplier(self):
        result = DamageCalculationService.calculate_direct(standard_values())

        self.assertAlmostEqual(190.0, result.attack)
        self.assertAlmostEqual(1_120.0, result.health)
        self.assertAlmostEqual(310.0, result.defense)
        self.assertAlmostEqual(190.0, result.scaling_attribute_value)
        self.assertAlmostEqual(1.50, result.damage_increase_multiplier)
        self.assertAlmostEqual(1.20, result.vulnerability_multiplier)
        self.assertAlmostEqual(1.25, result.critical_multiplier)
        self.assertAlmostEqual(114.0, result.enemy_defense)
        self.assertAlmostEqual(200.0 / 314.0, result.defense_multiplier)
        self.assertAlmostEqual(-0.05, result.effective_resistance)
        self.assertAlmostEqual(1 - (-0.05 / 1.10), result.resistance_multiplier)
        self.assertAlmostEqual(1.50, result.independent_multiplier)
        self.assertAlmostEqual(
            2.0
            * 190.0
            * 1.50
            * 1.25
            * (200.0 / 314.0)
            * (1 - (-0.05 / 1.10))
            * 1.20
            * 1.50,
            result.damage,
        )

    def test_dot_uses_fixed_50_percent_crit_and_settles_each_stack(self):
        result = DamageCalculationService.calculate_dot(
            DotDamageInput(
                damage=standard_values(crit_rate=0.10),
                remaining_durations=(2.0, 1.5),
                max_stacks=3,
            )
        )

        expected_tick = DamageCalculationService.calculate_direct(
            standard_values(crit_rate=0.50)
        ).damage
        self.assertAlmostEqual(expected_tick, result.tick_damage)
        self.assertEqual(2, result.stack_count)
        self.assertAlmostEqual(expected_tick * 2.0, result.settlement_layers[0].damage)
        self.assertAlmostEqual(expected_tick * 1.5, result.settlement_layers[1].damage)
        self.assertAlmostEqual(expected_tick * 3.5, result.settlement_damage)

    def test_dot_rejects_stack_count_above_maximum(self):
        with self.assertRaises(ValueError):
            DamageCalculationService.calculate_dot(
                DotDamageInput(
                    damage=standard_values(),
                    remaining_durations=(1.0, 1.0),
                    max_stacks=1,
                )
            )

    def test_ring_owner_and_reusable_ring_multipliers(self):
        owner = select_ring_owner(
            RingCharacter("low", level_multiplier=9_000, ring_strength=100),
            RingCharacter("high", level_multiplier=8_000, ring_strength=120),
        )

        self.assertEqual("high", owner.character_id)
        self.assertAlmostEqual(1.20, calculate_ring_strength_multiplier(120))
        self.assertAlmostEqual(24 * 120 / 300, calculate_ring_amplification(120))

    def test_topple_damage_uses_only_its_five_confirmed_multipliers(self):
        result = DamageCalculationService.calculate_topple(
            ToppleDamageInput(
                level_multiplier=3_603,
                mitigation=standard_values(),
                team_topple_strength=100,
                topple_damage_increases=(0.10, 0.05),
                enemy_topple_limit=50,
            )
        )

        self.assertAlmostEqual(200.0 / 314.0, result.defense_multiplier)
        self.assertAlmostEqual(1 - (-0.05 / 1.10), result.resistance_multiplier)
        self.assertAlmostEqual(
            3_603 * (1 + 100 / 300 + 0.10 + 0.05) * (50 / 3) * (200.0 / 314.0) * (1 - (-0.05 / 1.10)),
            result.damage,
        )

    def test_skill_can_scale_from_health_or_defense(self):
        health_result = DamageCalculationService.calculate_direct(
            standard_values(scaling_stat=DamageScalingStat.HEALTH)
        )
        defense_result = DamageCalculationService.calculate_direct(
            standard_values(scaling_stat=DamageScalingStat.DEFENSE)
        )

        self.assertAlmostEqual(1_120.0, health_result.scaling_attribute_value)
        self.assertAlmostEqual(310.0, defense_result.scaling_attribute_value)

    def test_open_world_uses_its_own_enemy_defense_base(self):
        outer_realm = DamageCalculationService.calculate_direct(standard_values())
        open_world = DamageCalculationService.calculate_direct(
            standard_values(scene=DamageScene.OPEN_WORLD)
        )

        self.assertAlmostEqual(120.0, open_world.enemy_defense)
        self.assertLess(open_world.defense_multiplier, outer_realm.defense_multiplier)

    def test_rejects_invalid_multiplier_and_level(self):
        with self.assertRaises(ValueError):
            DamageCalculationService.calculate_direct(standard_values(skill_multiplier=-0.1))

        with self.assertRaises(ValueError):
            DamageCalculationService.calculate_direct(standard_values(enemy_level=-1))

    def test_confirmed_five_level_reaction_mapping_uses_all_16_tiers(self):
        tiers = tuple(float(index) for index in range(16))
        self.assertEqual(0, reaction_tier_for_character_level(1))
        self.assertEqual(0, reaction_tier_for_character_level(5))
        self.assertEqual(1, reaction_tier_for_character_level(6))
        self.assertEqual(15, reaction_tier_for_character_level(80))
        self.assertEqual(15.0, reaction_multiplier_for_character_level(80, tiers))

    def test_skill_effective_level_includes_only_confirmed_third_awakening_bonus(self):
        self.assertEqual(10, effective_skill_level(10, 2))
        self.assertEqual(11, effective_skill_level(10, 3))
        self.assertEqual(10, skill_tier_for_effective_level(11))
        self.assertEqual(14, skill_tier_for_effective_level(99))

    def test_default_reaction_state_rules_refresh_and_dark_star_owners_are_independent(self):
        state = DarkStarInstances().apply("first", now=0.0, duration=5.0)
        state = state.apply("second", now=1.0, duration=5.0)
        state = state.apply("first", now=2.0, duration=5.0)
        self.assertEqual(("second",), tuple(item.owner_id for item in state.expired(6.0)))
        self.assertEqual(("first",), tuple(item.owner_id for item in state.remove_expired(6.0).instances))

    def test_dissonance_and_weave_use_confirmed_project_defaults(self):
        self.assertAlmostEqual(7.5, DamageCalculationService.calculate_dissonance_topple_reduction(50))
        self.assertAlmostEqual(
            100.0 * 0.20 * (24 * 120 / 300),
            calculate_weave_followup_damage(100.0, 120),
        )


if __name__ == "__main__":
    unittest.main()
