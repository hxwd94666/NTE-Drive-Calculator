# 测试角色面板属性计算引擎。
import unittest

from src.features.role.stat_engine import CharacterStatContext, build_character_panel


class CharacterStatEngineTests(unittest.TestCase):
    def _context(self, *, custom_weapons=None):
        return CharacterStatContext(
            role_models={
                "测试角色": {
                    "sub_stats": {"攻击力白值": 100, "暴击率": 5},
                    "level_sub_stats": {"1": {"攻击力白值": 10}, "80": {"攻击力白值": 100}},
                    "weapon": {
                        "name": "默认弧盘",
                        "sub_stats": {"攻击力白值": 50, "暴击率": 10},
                        "skill": [{"key": "攻击力%", "value": 999, "cover": 1}],
                    },
                }
            },
            roles_db={
                "测试角色": {
                    "extra_shape_label": "3格",
                    "extra_shape_buffs": {"攻击力%": 4, "暴击伤害%": 2},
                }
            },
            weapons_db={
                "自定义弧盘": {
                    "level": 80,
                    "level_sub_stats": {"80": {"攻击力白值": 200, "暴击伤害%": 20}},
                    "skill": [{"key": "攻击力%", "value": 500, "cover": 1}],
                }
            },
            shape_areas={"H_3": 3},
            stats_config={"tape_main_stat_values": {"攻击力%": 37.5}},
            stat_alias_mapping={},
            custom_weapons=custom_weapons or {},
        )

    def test_default_uses_model_full_level_and_default_weapon(self):
        panel = build_character_panel(self._context(), "测试角色")
        self.assertEqual(80, panel.role_level)
        self.assertEqual("默认弧盘", panel.weapon_name)
        self.assertEqual(150.0, panel.totals["总攻击力"])
        self.assertEqual(15.0, panel.totals["暴击率"])
        self.assertNotIn("攻击力%", panel.totals)

    def test_custom_weapon_overrides_model_weapon_for_execution(self):
        panel = build_character_panel(self._context(custom_weapons={"测试角色": "自定义弧盘"}), "测试角色")
        self.assertEqual("自定义弧盘", panel.weapon_name)
        self.assertEqual(300.0, panel.totals["总攻击力"])
        self.assertEqual(20.0, panel.totals["暴击伤害%"])

    def test_equipment_includes_shape_bonus_and_all_extra_shape_stats(self):
        panel = build_character_panel(
            self._context(),
            "测试角色",
            tape={"main_stats": "攻击力%", "quality": "Gold", "sub_stats": {"攻击力": 20}},
            drives=[{"shape_id": "H_3", "sub_stats": {"攻击力%": 10}}],
        )
        # (100 + 50) * (1 + (37.5 + 10 + 4) / 100) + (20 + 3 * 21)
        self.assertEqual(310.25, panel.totals["总攻击力"])
        self.assertEqual(840.0, panel.totals["总生命值"])
        self.assertEqual(2.0, panel.totals["暴击伤害%"])

    def test_drive_shape_main_stats_scale_with_quality(self):
        purple = build_character_panel(
            self._context(),
            "测试角色",
            drives=[{"shape_id": "H_3", "quality": "Purple", "sub_stats": {}}],
        )
        blue = build_character_panel(
            self._context(),
            "测试角色",
            drives=[{"shape_id": "H_3", "quality": "blue", "sub_stats": {}}],
        )

        self.assertEqual(672.0, purple.totals["总生命值"])
        self.assertEqual(504.0, blue.totals["总生命值"])
        self.assertEqual(206.4, purple.totals["总攻击力"])
        self.assertEqual(193.8, blue.totals["总攻击力"])

    def test_saved_tape_main_value_is_used_without_a_quality_fallback(self):
        panel = build_character_panel(
            self._context(),
            "测试角色",
            tape={"main_stats": "攻击力%", "main_value": 12.5, "quality": "Gold", "sub_stats": {}},
            drives=[],
        )
        # The saved snapshot value is authoritative; do not substitute the old
        # generic Gold fallback of 37.5%.
        self.assertEqual(168.75, panel.totals["总攻击力"])

    def test_conditional_weapon_skill_is_not_a_panel_stat(self):
        panel = build_character_panel(self._context(), "测试角色")
        self.assertEqual(150.0, panel.totals["总攻击力"])

    def test_marginal_benefit_uses_ability_damage_term(self):
        from src.features.role import core

        original_load_stats = core.load_stats
        try:
            core.load_stats = lambda: {"benefit_one": {"异能伤害%": 1.25}}
            _base, items = core.calc_marginal_benefits(
                {
                    "攻击力白值": 100.0,
                    "攻击力%": 0.0,
                    "攻击力": 0.0,
                    "异能伤害%": 10.0,
                    "伤害增加%": 0.0,
                    "暴击率%": 0.0,
                    "暴击伤害%": 0.0,
                }
            )
        finally:
            core.load_stats = original_load_stats

        names = [item[0] for item in items]
        self.assertIn("异能伤害%", names)
        self.assertNotIn("元素" + "伤害%", names)


if __name__ == "__main__":
    unittest.main()
