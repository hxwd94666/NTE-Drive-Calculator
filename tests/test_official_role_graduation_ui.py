# 验证毕业率仅恢复到角色边际收益标题栏，并保持原计算与排列方式。
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from src.features.official_role import role_calculation


class OfficialRoleGraduationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_graduation_rate_is_left_of_direct_damage_score(self) -> None:
        detail = {
            "profile": {},
            "property_weights": {},
            "main_property_weights": {},
        }
        editor: dict = {}
        margins = {"damage": 50.0, "rows": []}
        final_weights = {
            "property_weights": {},
            "main_property_weights": {},
            "formula_property_ids": frozenset(),
        }
        with (
            patch.object(
                role_calculation,
                "graduation_benchmark_damage",
                return_value=100.0,
            ),
            patch.object(
                role_calculation,
                "_graduation_tooltip",
                return_value="原毕业基准说明",
            ),
            patch.object(
                role_calculation,
                "calculate_official_role_margins",
                return_value=margins,
            ),
            patch.object(
                role_calculation,
                "calculate_official_role_final_weights",
                return_value=final_weights,
            ),
        ):
            group = role_calculation._build_margin_group(
                object(),
                1003,
                detail,
                editor,
            )

        header = group.layout().itemAt(0).layout()
        graduation = header.itemAt(0).widget()
        damage = header.itemAt(1).widget()
        self.assertIsInstance(graduation, QLabel)
        self.assertIsInstance(damage, QLabel)
        self.assertEqual("officialRoleGraduationRate", graduation.objectName())
        self.assertEqual("直伤毕业率 : 50.0%", graduation.text())
        self.assertEqual("原毕业基准说明", graduation.toolTip())
        self.assertEqual("officialRoleDamageScore", damage.objectName())
        self.assertEqual("直伤评分 : 50.00", damage.text())


if __name__ == "__main__":
    unittest.main()
