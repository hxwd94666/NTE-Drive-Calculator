# 验证角色伤害环形图中心标题和数值不会使用重叠区域。
from __future__ import annotations

import unittest

from PySide6.QtCore import QRectF

from src.features.battle_report.role_contribution_view import (
    _donut_center_text_rects,
)


class BattleReportRoleContributionUiTests(unittest.TestCase):
    def test_donut_center_title_and_value_bands_do_not_overlap(self) -> None:
        for hole_size in (66.0, 85.0, 96.0):
            with self.subTest(hole_size=hole_size):
                hole = QRectF(10.0, 20.0, hole_size, hole_size)
                title, value = _donut_center_text_rects(hole)

                self.assertTrue(hole.contains(title))
                self.assertTrue(hole.contains(value))
                self.assertLess(title.bottom(), value.top())


if __name__ == "__main__":
    unittest.main()
