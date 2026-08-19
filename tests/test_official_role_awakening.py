# 验证觉醒多选、共鸣门槛和旧账号兼容映射。
import unittest

from src.services.official_role_awakening_service import (
    active_awaken_effects,
    awaken_skill_level_delta,
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
