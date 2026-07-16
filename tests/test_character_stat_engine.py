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

    def test_conditional_weapon_skill_is_not_a_panel_stat(self):
        panel = build_character_panel(self._context(), "测试角色")
        self.assertEqual(150.0, panel.totals["总攻击力"])


if __name__ == "__main__":
    unittest.main()
