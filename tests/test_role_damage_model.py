# 覆盖角色直伤公式和边际收益的基础行为。
import unittest
from types import SimpleNamespace


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

    def test_graduation_rate_precedes_direct_damage_score(self):
        from src.features.role.marginal_widget import MarginalBenefitPanel

        panel = SimpleNamespace(
            graduation_benchmark=SimpleNamespace(damage=200.0), base_damage=150.0,
        )

        self.assertEqual("直伤毕业率 : 75.0%", MarginalBenefitPanel._graduation_label_text(panel))
        self.assertEqual("直伤评分 : 150.00", MarginalBenefitPanel._damage_label_text(panel))

    def test_role_detail_prefers_sqlite_mapped_equipment_fields(self):
        from src.features.role.drive_widget import _resolve_display_equipment

        displayed = _resolve_display_equipment(
            {
                "uid": "nte-module-3-9",
                "shape_id": "HENG3",
                "sub_stats": {"AtkAdd": 20},
                "quality": "Purple",
            },
            {
                "nte-module-3-9": {
                    "uid": "nte-module-3-9",
                    "shape_id": "H_3",
                    "sub_stats": {"攻击力": 20.0, "暴击率%": 2.5},
                    "quality": "Gold",
                },
            },
        )

        self.assertEqual("H_3", displayed["shape_id"])
        self.assertEqual({"攻击力": 20.0, "暴击率%": 2.5}, displayed["sub_stats"])
        self.assertEqual("Gold", displayed["quality"])


if __name__ == "__main__":
    unittest.main()
