# 验证所有评分入口共享的评级配色层级。
from __future__ import annotations

import unittest

from src.app.theme import GRADE_BGS, GRADE_COLORS
from src.features.identification.page import GRADE_COLORS as IDENTIFY_GRADE_COLORS


class GradeThemeTests(unittest.TestCase):
    def test_s_and_ss_use_the_former_top_tier_color(self) -> None:
        self.assertEqual("#ffa726", GRADE_COLORS["S"])
        self.assertEqual("#ffa726", GRADE_COLORS["SS"])
        self.assertEqual("#ffa72618", GRADE_BGS["S"])
        self.assertEqual("#ffa72618", GRADE_BGS["SS"])

    def test_sss_and_ace_use_the_former_s_tier_color_everywhere(self) -> None:
        self.assertEqual("#f0883e", GRADE_COLORS["SSS"])
        self.assertEqual("#f0883e", GRADE_COLORS["ACE"])
        self.assertIs(GRADE_COLORS, IDENTIFY_GRADE_COLORS)


if __name__ == "__main__":
    unittest.main()
