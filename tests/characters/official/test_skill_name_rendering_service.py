# 验证 GA/GE 身份、已审覆盖与未知传播；名称投影不改原始来源字段。
import unittest

from src.services.official_role.skill_names import (
    SkillNameRenderingService,
    project_immediate_nightmare_source_names,
)


class SkillNameRenderingServiceTests(unittest.TestCase):
    def setUp(self):
        self.renderer = SkillNameRenderingService(
            ability_rows=[
                {"ability_id": "GA_Lacrimosa_Melee", "name_zh": "酸甜口味的制裁"},
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

    def test_bundled_sagiri_devour_is_named_without_fake_formula_ability(self):
        renderer = SkillNameRenderingService.from_static_database()

        identity = renderer.render_axis_identity(
            ability_id=None,
            damage_id=None,
            gameplay_effect_index=None,
            gameplay_effect_name="GE_Player_Sagiri_Branch_Kill_Damage",
            attack_type="其他",
        )

        self.assertEqual("摄食模式", identity.skill_name)
        self.assertEqual("摄食模式", identity.damage_name)
        self.assertEqual(
            "",
            renderer.resolve_ability_id(
                None,
                "GE_Player_Sagiri_Branch_Kill_Damage",
            ),
        )
        self.assertEqual(
            "摄食模式",
            renderer.resolve_attack_type(
                "GE_Player_Sagiri_Branch_Kill_Damage",
                captured="其他",
            ),
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

        g_damage = tuple(
            renderer.render_axis_identity(
                ability_id="GA_Lacrimosa_Melee",
                damage_id=None,
                gameplay_effect_index=None,
                gameplay_effect_name=effect_id,
                attack_type="普攻",
            )
            for effect_id in (
                "GE_Player_Lacrimosa_Pan_Damage",
                "GE_Player_Lacrimosa_SwitchModB_Damage",
            )
        )
        passive_damage = renderer.render_axis_identity(
            ability_id=None,
            damage_id=None,
            gameplay_effect_index=3241,
            gameplay_effect_name="GE_Player_Lacrimosa_AnHunZhouTwo_Damage",
            attack_type="失谐",
        )

        self.assertEqual(
            (("G技能", "G技能伤害"),) * 2,
            tuple((row.skill_name, row.damage_name) for row in g_damage),
        )
        self.assertEqual(
            ("被动：番茄酱盛宴", "番茄酱盛宴 · 失谐追加伤害"),
            (passive_damage.skill_name, passive_damage.damage_name),
        )

    def test_immediate_nightmare_inherits_g_display_without_rewriting_identity(self):
        hits = [{
            "sequence_order": 2,
            "relative_time_us": 2_000_000,
            "character_id": 1004,
            "target_id": "target-1",
            "damage": 100.0,
            "gameplay_effect_name": "GE_Player_Lacrimosa_Pan_Damage",
            "ability_display_name": "G技能",
        }, {
            "sequence_order": 3,
            "relative_time_us": 2_014_815,
            "character_id": 1004,
            "target_id": "target-1",
            "damage": 50.0,
            "gameplay_effect_name": "GE_Player_Lacrimosa_Blood_Damage_LV6",
            "ability_name": "GA_Lacrimosa_Melee",
            "ability_display_name": "普通攻击：酸甜口味的制裁",
        }]

        project_immediate_nightmare_source_names(hits)

        self.assertEqual("G技能", hits[1]["ability_display_name"])
        self.assertEqual("GA_Lacrimosa_Melee", hits[1]["ability_name"])

