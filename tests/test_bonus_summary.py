# 测试分配结果属性汇总与对比逻辑。
import unittest

from src.features.allocation.bonus_summary import (
    BonusSummaryContext,
    aligned_bonus_comparison_rows,
    bonus_rows_for_mode,
    has_bonus_delta,
    merge_bonus_row_lists,
    split_loadout_sources,
    synthesize_character_bonus_rows,
)


class BonusSummaryTests(unittest.TestCase):
    def test_split_loadout_sources_separates_tape_and_drives(self):
        tape, drives = split_loadout_sources(
            [
                {"type": "tape", "main_stats": "攻击力%", "uid": "t1"},
                {"type": "drive", "shape_id": "H_3", "uid": "d1"},
            ]
        )
        self.assertEqual("t1", tape["uid"])
        self.assertEqual(["d1"], [drive["uid"] for drive in drives])

    def test_synthesize_character_bonus_rows_combines_panel_stats(self):
        rows = synthesize_character_bonus_rows(
            [
                ("攻击力白值", 1000),
                ("攻击力%", 50),
                ("攻击力", 120),
            ]
        )
        self.assertEqual([("总攻击力", 1620.0), ("小攻击", 120.0)], rows)

    def test_aligned_bonus_comparison_rows_reports_delta(self):
        aligned = aligned_bonus_comparison_rows(
            [("暴击率%", 30.0)],
            [("暴击率%", 36.5)],
        )
        self.assertEqual(1, len(aligned))
        self.assertEqual(6.5, aligned[0]["delta"])
        self.assertTrue(has_bonus_delta(aligned[0]))

    def test_bonus_rows_for_mode_character_includes_role_and_equipment(self):
        ctx = BonusSummaryContext(
            roles_db={"A": {"sub_stats": {"生命值%": 10}}},
            shape_areas={},
            stats_config={},
            stat_alias_mapping={},
        )
        rows = bonus_rows_for_mode(
            ctx,
            "A",
            None,
            [{"sub_stats": {"暴击率%": 5.0}, "shape_id": "H_3"}],
            mode="character",
        )
        self.assertTrue(any(stat == "暴击率%" for stat, _value in rows))

    def test_merge_bonus_row_lists_sums_same_stat(self):
        ctx = BonusSummaryContext({}, {}, {}, {})
        rows = merge_bonus_row_lists(ctx, [("暴击率%", 10)], [("暴击率%", 5)])
        self.assertEqual([("暴击率%", 15.0)], rows)
