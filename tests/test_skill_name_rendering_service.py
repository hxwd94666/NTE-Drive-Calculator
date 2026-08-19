# 验证稳定 GA/GE 标识只在展示层解析为官方中文技能名。
import unittest

from src.services.skill_name_rendering_service import SkillNameRenderingService


class SkillNameRenderingServiceTests(unittest.TestCase):
    def setUp(self):
        self.renderer = SkillNameRenderingService(
            ability_rows=[
                {"ability_id": "GA_Lacrimosa_Melee", "name_zh": "酸甜口味的制裁"},
                {"ability_id": "GA_Lacrimosa_Evade", "name_zh": "1"},
            ],
            damage_bindings=[{
                "damage_id": "GE_Player_Lacrimosa_Blood_Damage_LV6",
                "ability_id": "GA_Lacrimosa_Melee",
            }],
        )

    def test_ability_name_uses_official_chinese_catalog(self):
        self.assertEqual(
            "普通攻击：酸甜口味的制裁",
            self.renderer.render_ability_name("GA_Lacrimosa_Melee"),
        )

    def test_damage_name_resolves_through_stable_ability_identity(self):
        self.assertEqual(
            "酸甜口味的制裁",
            self.renderer.resolve_damage_name(
                "GE_Player_Lacrimosa_Blood_Damage_LV6"
            ),
        )

    def test_invalid_catalog_text_falls_back_to_stable_category(self):
        self.assertEqual(
            "GA_Lacrimosa_Evade",
            self.renderer.render_ability_name("GA_Lacrimosa_Evade"),
        )
