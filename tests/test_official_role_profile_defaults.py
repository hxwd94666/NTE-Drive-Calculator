# 验证新角色页默认不选觉醒，并把九次升级解释为基础技能十级。
import unittest

from src.services.official_role_attribute_service import _default_profile


class OfficialRoleProfileDefaultTests(unittest.TestCase):
    def test_default_profile_has_level_ten_skills_and_no_awakenings(self):
        profile = _default_profile(
            {"character_id": 1004},
            [{
                "level": 80,
                "breakthrough_stage": 6,
                "hp_base": 1.0,
                "atk_base": 1.0,
                "def_base": 1.0,
            }],
            [],
            [{
                "skill_id": "GA_Lacrimosa_Melee",
                "levels": [{
                    "level": 9,
                    "required_breakthrough_stage": 6,
                    "required_awaken_level": 0,
                }],
                "damage_entries": [{"damage_id": "Damage"}],
            }],
            [{
                "effect_id": "Effect1",
                "awaken_type": "Awaken_Effect",
            }],
            0,
        )

        self.assertEqual(0, profile["awakening_level"])
        self.assertEqual([], profile["selected_awaken_effect_ids"])
        self.assertEqual(10, profile["skill_levels"]["GA_Lacrimosa_Melee"])
