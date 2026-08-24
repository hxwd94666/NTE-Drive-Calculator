# 验证稳定 GA/GE 标识只在展示层解析为官方中文技能名。
import unittest

from src.services.skill_name_rendering_service import (
    SkillNameRenderingService,
    preferred_battle_damage_name,
    render_battle_event_type,
)


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
                "damage_type": "CHAOS",
            }, {
                "damage_id": "GE_boss_018_act019_Steal_Dmg_BP",
                "ability_id": "GA_Lacrimosa_Melee",
                "damage_type": "CHAOS",
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
                {
                    "damage_id": "Buff_Reaction_5_new_1036",
                    "damage_name_zh": "浊燃",
                    "show_parent_ability": False,
                },
            ],
            gameplay_effect_rows=[
                {
                    "gameplay_effect_index": 4503,
                    "gameplay_effect_id": "Buff_Reaction_5_new_1036",
                }
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

    def test_stolen_boss_skill_uses_its_exact_damage_semantic(self):
        renderer = SkillNameRenderingService(
            ability_rows=[
                {"ability_id": "GA_Lacrimosa_Skill", "name_zh": "起床气加载中"}
            ],
            semantic_rows=[
                {
                    "damage_id": "GE_boss_018_act019_Steal_Dmg_BP",
                    "ability": "GA_Lacrimosa_Skill",
                    "damage_name_zh": "番茄酱恶魔",
                    "show_parent_ability": False,
                }
            ],
        )

        identity = renderer.render_axis_identity(
            ability_id="GA_Lacrimosa_Skill",
            damage_id="",
            gameplay_effect_index=None,
            gameplay_effect_name="GE_boss_018_act019_Steal_Dmg_BP",
        )

        self.assertEqual("番茄酱恶魔", identity.damage_name)

    def test_enemy_projectile_reflection_has_a_public_damage_name(self):
        renderer = SkillNameRenderingService(
            ability_rows=[],
            semantic_rows=[
                {
                    "damage_id": "GE_boss_05_HitBullet_Dmg_BP",
                    "damage_name_zh": "敌方飞弹反射伤害",
                    "show_parent_ability": False,
                }
            ],
        )

        identity = renderer.render_axis_identity(
            ability_id=None,
            damage_id=None,
            gameplay_effect_index=1349,
            gameplay_effect_name="GE_boss_05_HitBullet_Dmg_BP",
        )

        self.assertEqual("敌方飞弹反射伤害", identity.damage_name)

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

    def test_missing_captured_attribute_uses_static_damage_type(self):
        self.assertEqual(
            "chaos",
            self.renderer.resolve_damage_attribute(
                "GE_Player_Lacrimosa_Blood_Damage_LV6",
                captured="unknown",
            ),
        )
        self.assertEqual(
            "nature",
            self.renderer.resolve_damage_attribute(
                "GE_Player_Lacrimosa_Blood_Damage_LV6",
                captured="nature",
            ),
        )

    def test_topple_classification_does_not_render_as_unknown_reaction(self):
        self.assertEqual("倾陷伤害", render_battle_event_type("topple"))

    def test_reflected_enemy_projectile_renders_as_mechanic_damage(self):
        self.assertEqual("机制伤害", render_battle_event_type("mechanic"))

    def test_damage_item_precedes_its_source_skill(self):
        self.assertEqual(
            "蚀心",
            preferred_battle_damage_name("蚀心", "普通攻击：燎原"),
        )
        self.assertEqual(
            "普通攻击：燎原",
            preferred_battle_damage_name("未识别伤害", "普通攻击：燎原"),
        )
        self.assertEqual(
            "变轨技能：归航",
            preferred_battle_damage_name("未知伤害", "变轨技能：归航"),
        )
        self.assertEqual(
            "GA_Test_Skill",
            preferred_battle_damage_name(
                "未知伤害",
                "未知技能",
                "GA_Test_Skill",
            ),
        )
        self.assertEqual(
            "未识别技能",
            preferred_battle_damage_name("未知伤害", "未知技能"),
        )

    def test_invalid_catalog_text_falls_back_to_stable_category(self):
        self.assertEqual(
            "GA_Lacrimosa_Evade",
            self.renderer.render_ability_name("GA_Lacrimosa_Evade"),
        )

    def test_axis_identity_uses_ge_index_when_ability_and_damage_are_empty(self):
        identity = self.renderer.render_axis_identity(
            ability_id=None,
            damage_id=None,
            gameplay_effect_index=4503,
            gameplay_effect_name=None,
            damage_component="浊燃",
            attack_type="浊燃",
        )

        self.assertEqual("浊燃", identity.skill_name)
        self.assertEqual("浊燃", identity.damage_name)
        self.assertEqual("Buff_Reaction_5_new_1036", identity.gameplay_effect_id)

    def test_axis_identity_hides_unmapped_ge_behind_official_ability(self):
        identity = self.renderer.render_axis_identity(
            ability_id=None,
            damage_id=None,
            gameplay_effect_index=None,
            gameplay_effect_name="GE_boss_018_act019_Steal_Dmg_BP",
            damage_component=None,
            attack_type="E技能",
        )

        self.assertEqual("普通攻击：酸甜口味的制裁", identity.skill_name)
        self.assertEqual("酸甜口味的制裁", identity.damage_name)
        self.assertEqual(
            "GE_boss_018_act019_Steal_Dmg_BP",
            identity.gameplay_effect_id,
        )

    def test_axis_identity_does_not_expose_unknown_technical_ge(self):
        identity = self.renderer.render_axis_identity(
            ability_id=None,
            damage_id=None,
            gameplay_effect_index=None,
            gameplay_effect_name="GE_boss_05_act10_Dmg01_BP",
            damage_component=None,
            attack_type="其他",
        )

        self.assertEqual("未识别技能", identity.skill_name)
        self.assertEqual("未识别伤害", identity.damage_name)
        self.assertEqual("GE_boss_05_act10_Dmg01_BP", identity.gameplay_effect_id)

    def test_lacrimosa_g_damage_overrides_incorrect_melee_binding(self):
        renderer = SkillNameRenderingService(
            ability_rows=[{
                "ability_id": "GA_Lacrimosa_Melee",
                "name_zh": "酸甜口味的制裁",
            }],
            damage_bindings=[{
                "damage_id": "GE_Player_Lacrimosa_SwitchModB_Damage",
                "ability_id": "GA_Lacrimosa_Melee",
                "damage_type": "CHAOS",
            }],
            semantic_rows=[{
                "damage_id": "GE_Player_Lacrimosa_SwitchModB_Damage",
                "ability": "GA_Lacrimosa_SwitchSkill",
                "override_observed_ability": True,
                "override_observed_attack_type": True,
                "attack_type": "G技能",
                "damage_name_zh": "G技能伤害",
                "show_parent_ability": False,
            }],
        )

        identity = renderer.render_axis_identity(
            ability_id="GA_Lacrimosa_Melee",
            damage_id=None,
            gameplay_effect_index=None,
            gameplay_effect_name="GE_Player_Lacrimosa_SwitchModB_Damage",
            attack_type="普攻",
        )

        self.assertEqual("G技能", identity.skill_name)
        self.assertEqual("G技能伤害", identity.damage_name)
        self.assertEqual(
            "GA_Lacrimosa_SwitchSkill",
            renderer.resolve_ability_id(
                "GA_Lacrimosa_Melee",
                "GE_Player_Lacrimosa_SwitchModB_Damage",
            ),
        )
        self.assertEqual(
            "G技能",
            renderer.resolve_attack_type(
                "GE_Player_Lacrimosa_SwitchModB_Damage",
                captured="普攻",
            ),
        )

    def test_lacrimosa_dissonance_extra_uses_official_passive_name(self):
        renderer = SkillNameRenderingService(
            ability_rows=[{
                "ability_id": "GA_Lacrimosa_Passive_1",
                "name_zh": "番茄酱盛宴",
            }],
            semantic_rows=[{
                "damage_id": "GE_Player_Lacrimosa_AnHunZhouTwo_Damage",
                "ability": "GA_Lacrimosa_Passive_1",
                "damage_name_zh": "失谐追加伤害",
                "show_parent_ability": True,
            }],
        )

        identity = renderer.render_axis_identity(
            ability_id=None,
            damage_id=None,
            gameplay_effect_index=None,
            gameplay_effect_name="GE_Player_Lacrimosa_AnHunZhouTwo_Damage",
            attack_type="失谐",
        )

        self.assertEqual("被动：番茄酱盛宴", identity.skill_name)
        self.assertEqual("番茄酱盛宴 · 失谐追加伤害", identity.damage_name)

    def test_fully_missing_hit_identity_is_labeled_as_unattributed(self):
        identity = self.renderer.render_axis_identity(
            ability_id=None,
            damage_id=None,
            gameplay_effect_index=None,
            gameplay_effect_name=None,
            damage_component=None,
            attack_type=None,
        )

        self.assertEqual("未归因伤害", identity.skill_name)
        self.assertEqual("来源字段缺失", identity.damage_name)

    def test_bundled_lacrimosa_semantics_cover_g_and_dissonance_passive(self):
        renderer = SkillNameRenderingService.from_static_database()

        g_damage = renderer.render_axis_identity(
            ability_id="GA_Lacrimosa_Melee",
            damage_id=None,
            gameplay_effect_index=3276,
            gameplay_effect_name="GE_Player_Lacrimosa_SwitchModB_Damage",
            attack_type="普攻",
        )
        passive_damage = renderer.render_axis_identity(
            ability_id=None,
            damage_id=None,
            gameplay_effect_index=3241,
            gameplay_effect_name="GE_Player_Lacrimosa_AnHunZhouTwo_Damage",
            attack_type="失谐",
        )

        self.assertEqual(("G技能", "G技能伤害"), (
            g_damage.skill_name,
            g_damage.damage_name,
        ))
        self.assertEqual(
            ("被动：番茄酱盛宴", "番茄酱盛宴 · 失谐追加伤害"),
            (passive_damage.skill_name, passive_damage.damage_name),
        )

    def test_battle_event_type_localizes_protocol_tokens(self):
        self.assertEqual(
            "直伤 · 变轨技能 · 灵属性伤害",
            render_battle_event_type("direct", "E技能", "NATURE"),
        )
