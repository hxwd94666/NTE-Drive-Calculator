# 验证觉醒多选、共鸣门槛和旧账号兼容映射。
import unittest

from src.services.official_role_awakening_service import (
    active_awaken_effects,
    awaken_skill_level_delta,
    render_awaken_effect_description,
    resolve_awakening_profile,
)


def _effects():
    return [
        {
            "effect_id": f"Effect{index}",
            "awaken_type": "Awaken_Effect",
            "skill_level_bonuses": [],
        }
        for index in range(1, 7)
    ] + [
        {
            "effect_id": "resonance_3",
            "awaken_type": "Awaken_Resonance",
            "skill_level_bonuses": [{"skill_id": "Skill", "level_delta": 1}],
        },
        {
            "effect_id": "resonance_6",
            "awaken_type": "Awaken_Resonance",
            "skill_level_bonuses": [{"skill_id": "Ultra", "level_delta": 1}],
        },
    ]


class OfficialRoleAwakeningTests(unittest.TestCase):
    def test_legacy_numeric_awakening_resolves_to_ordered_selection(self):
        profile = resolve_awakening_profile({"awakening_level": 3}, _effects())
        self.assertEqual(
            ["Effect1", "Effect2", "Effect3"],
            profile["selected_awaken_effect_ids"],
        )
        self.assertTrue(profile["awakening_selection_initialized"])

    def test_three_selected_effects_activate_only_three_effect_resonance(self):
        profile = {
            "awakening_level": 3,
            "awakening_selection_initialized": True,
            "selected_awaken_effect_ids": ["Effect1", "Effect4", "Effect6"],
        }
        active_ids = [
            effect["effect_id"] for effect in active_awaken_effects(profile, _effects())
        ]
        self.assertEqual(
            ["Effect1", "Effect4", "Effect6", "resonance_3"],
            active_ids,
        )
        self.assertEqual(1, awaken_skill_level_delta(profile, _effects(), "Skill"))
        self.assertEqual(0, awaken_skill_level_delta(profile, _effects(), "Ultra"))

    def test_description_uses_base_level_ten_and_three_effect_bonus(self):
        effects = _effects()
        effect = {
            "description_zh": "当前技能等级下提升至<NumGreen>{0}%</>。",
            "description_damage_entries": [{
                "ability_id": "Skill",
                "atk_rate_base": [index / 10 for index in range(1, 13)],
                "def_rate_base": [],
                "hp_rate_base": [],
                "modifier_atk_rate_base_coefficient": None,
            }],
        }
        profile = {
            "skill_levels": {"Skill": 10},
            "awakening_selection_initialized": True,
            "selected_awaken_effect_ids": ["Effect1", "Effect4", "Effect6"],
        }

        self.assertEqual(
            "当前技能等级下提升至<NumGreen>110%</>。",
            render_awaken_effect_description(effect, profile, effects),
        )

    def test_description_does_not_apply_ft_attack_rate_coefficient(self):
        effects = _effects()
        effect = {
            "description_zh": "当前技能等级下倍率为<NumGreen>{0}%</>。",
            "description_damage_entries": [{
                "ability_id": "Skill",
                "atk_rate_base": [0.439],
                "def_rate_base": [],
                "hp_rate_base": [],
                "modifier_atk_rate_base_coefficient": 0.9,
            }],
        }
        profile = {
            "skill_levels": {"Skill": 1},
            "awakening_selection_initialized": True,
            "selected_awaken_effect_ids": [],
        }

        self.assertEqual(
            "当前技能等级下倍率为<NumGreen>43.9%</>。",
            render_awaken_effect_description(effect, profile, effects),
        )
