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
            semantic_rows=[
                {
                    "damage_id": "GE_Player_Lacrimosa_Blood_Damage_LV6",
                    "damage_name_zh": "「噩梦」",
                    "show_parent_ability": False,
                },
                {
                    "damage_id": "GE_Player_Lacrimosa_Melee1_Damage",
                    "damage_name_zh": "第一段",
                },
            ],
        )

    def test_ability_name_uses_official_chinese_catalog(self):
        self.assertEqual(
            "普通攻击：酸甜口味的制裁",
            self.renderer.render_ability_name("GA_Lacrimosa_Melee"),
        )

    def test_damage_semantic_can_hide_parent_ability_name(self):
        self.assertEqual(
            "「噩梦」",
            self.renderer.resolve_damage_name(
                "GE_Player_Lacrimosa_Blood_Damage_LV6"
            ),
        )

    def test_damage_semantic_can_include_parent_ability_name(self):
        self.assertEqual(
            "酸甜口味的制裁 · 第一段",
            self.renderer.resolve_damage_name(
                "GE_Player_Lacrimosa_Melee1_Damage",
                ability_id="GA_Lacrimosa_Melee",
            ),
        )

    def test_damage_name_without_semantic_falls_back_to_parent_ability(self):
        self.assertEqual(
            "酸甜口味的制裁",
            self.renderer.resolve_damage_name(
                "GE_Player_Lacrimosa_Other_Damage",
                ability_id="GA_Lacrimosa_Melee",
            ),
        )

    def test_damage_type_uses_chinese_element_name(self):
        self.assertEqual(
            "暗属性伤害",
            self.renderer.render_damage_type_name("CHAOS"),
        )

    def test_invalid_catalog_text_falls_back_to_stable_category(self):
        self.assertEqual(
            "GA_Lacrimosa_Evade",
            self.renderer.render_ability_name("GA_Lacrimosa_Evade"),
        )
