# 覆盖角色直伤公式和边际收益的基础行为。
import unittest


class RoleDamageModelTests(unittest.TestCase):
    def test_direct_damage_uses_attack_bonus_damage_bonus_and_crit(self):
        from src.features.role.damage_model import calc_direct_damage

        total_stats = {
            "攻击力白值": 100.0,
            "攻击力%": 50.0,
            "攻击力": 20.0,
            "异能伤害%": 30.0,
            "伤害增加%": 20.0,
            "暴击率%": 50.0,
            "暴击伤害%": 100.0,
        }

        self.assertAlmostEqual(382.5, calc_direct_damage(total_stats), places=4)

    def test_direct_damage_respects_crit_rate_cap(self):
        from src.features.role.damage_model import calc_direct_damage

        total_stats = {
            "攻击力白值": 100.0,
            "暴击率%": 120.0,
            "暴击伤害%": 100.0,
        }

        self.assertAlmostEqual(200.0, calc_direct_damage(total_stats), places=4)
        self.assertAlmostEqual(150.0, calc_direct_damage(total_stats, crit_rate_cap=50.0), places=4)

    def test_marginal_benefits_keep_expected_labels(self):
        from src.features.role.damage_model import ABILITY_DAMAGE_STAT, calc_direct_marginal_benefits

        total_stats = {"攻击力白值": 100.0, "暴击率%": 50.0, "暴击伤害%": 100.0}
        _base, margins = calc_direct_marginal_benefits(
            total_stats,
            {"攻击力白值": 1.0, "暴击率%": 1.0, "暴击伤害%": 2.0, ABILITY_DAMAGE_STAT: 1.25},
        )
        labels = {item[0] for item in margins}

        self.assertIn("攻击力白值", labels)
        self.assertIn("暴击率%", labels)
        self.assertIn(ABILITY_DAMAGE_STAT, labels)


if __name__ == "__main__":
    unittest.main()
