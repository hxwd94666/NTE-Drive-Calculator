# 测试分配结果属性汇总与对比逻辑。
import unittest

from src.features.allocation.bonus_summary import (
    BonusSummaryContext,
    aligned_bonus_comparison_rows,
    equipment_bonus_rows,
    synthesize_character_bonus_rows,
)


class BonusSummaryTests(unittest.TestCase):
    def test_synthesize_character_bonus_rows_combines_panel_stats(self):
        rows = synthesize_character_bonus_rows(
            [
                ("攻击力白值", 1000),
                ("攻击力%", 50),
                ("攻击力", 120),
            ]
        )
        self.assertEqual([("总攻击力", 1620.0), ("小攻击", 120.0)], rows)

    def test_same_crit_tape_is_not_reported_as_a_new_thirty_percent_gain(self):
        ctx = BonusSummaryContext({}, {}, {}, {})
        drives = [{"sub_stats": {"暴击率%": 30.0}}]
        old_rows = equipment_bonus_rows(
            ctx,
            "A",
            {"main_stats": "暴击率%", "quality": "Gold", "sub_stats": {}},
            drives,
        )
        new_rows = equipment_bonus_rows(
            ctx,
            "A",
            {
                "main_stats": "暴击率%",
                "main_value": 30.0,
                "quality": "Gold",
                "sub_stats": {},
            },
            drives,
        )

        self.assertEqual(old_rows, new_rows)
        self.assertEqual(
            0.0,
            aligned_bonus_comparison_rows(old_rows, new_rows)[0]["delta"],
        )
