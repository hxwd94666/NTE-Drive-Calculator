# 测试角色直伤毕业率理论基准计算。
import unittest

from src.features.role.graduation_model import calculate_graduation_benchmark


class GraduationModelTests(unittest.TestCase):
    def setUp(self):
        self.role_model = {
            "level_sub_stats": {
                "1": {"攻击力白值": 10, "暴击率%": 5, "暴击伤害%": 50},
                "80": {"攻击力白值": 100, "暴击率%": 5, "暴击伤害%": 50},
            },
            "sub_stats": {"攻击力白值": 100, "暴击率%": 5, "暴击伤害%": 50},
            "weapon": {"name": "专武"},
            "set_bonus": {"skill": {}, "skill_2": {}, "skill_cover": 0},
        }
        self.role_config = {
            "default_set": "测试套装",
            "weights": {"暴击率%": 1.0, "暴击伤害%": 0.9, "攻击力%": 0.8, "攻击力": 0.7},
            "main_weights": {"暴击率%": 1.0, "暴击伤害%": 1.0, "攻击力%": 0.8},
            "extra_shape_buffs": {"攻击力%": 5.0},
        }
        self.weapons = {
            "专武": {
                "level_sub_stats": {"80": {"攻击力白值": 100}},
                "mix_level_sub_stats": {"1": {"skill": []}},
            }
        }
        self.stats = {
            "gold_base_values": {"暴击率%": 1, "暴击伤害%": 2, "攻击力%": 1.25, "攻击力": 8},
            "tape_stat_values": {"暴击率%": 10, "暴击伤害%": 20, "攻击力%": 12.5, "攻击力": 80},
            "tape_main_stat_values": {"攻击力%": 37.5, "暴击伤害%": 60, "暴击率%": 30},
            "stat_alias_mapping": {},
        }

    def _benchmark(self, extra_shape_count):
        return calculate_graduation_benchmark(
            "测试角色",
            {"weights": self.role_config["weights"]},
            role_model=self.role_model,
            role_config=self.role_config,
            weapons_db=self.weapons,
            stats_config=self.stats,
            extra_shape_count=extra_shape_count,
        )

    def test_uses_full_twenty_cell_stats_and_signature_weapon(self):
        benchmark = self._benchmark(2)

        self.assertIsNotNone(benchmark)
        self.assertEqual("专武", benchmark.weapon_name)
        self.assertEqual(("暴击率%", "暴击伤害%", "攻击力%", "攻击力"), benchmark.drive_sub_stats)
        self.assertEqual(2, benchmark.extra_shape_count)
        self.assertGreater(benchmark.damage, 0)

    def test_extra_shape_count_from_fixed_blueprint_changes_the_reference(self):
        without_extra = self._benchmark(0)
        with_extra = self._benchmark(2)

        self.assertIsNotNone(without_extra)
        self.assertIsNotNone(with_extra)
        self.assertGreater(with_extra.damage, without_extra.damage)

    def test_main_stat_is_selected_by_direct_damage_not_display_order(self):
        benchmark = self._benchmark(0)

        self.assertIsNotNone(benchmark)
        self.assertIn(benchmark.tape_main_stat, self.stats["tape_main_stat_values"])
        self.assertEqual("暴击率%", benchmark.tape_main_stat)


if __name__ == "__main__":
    unittest.main()
