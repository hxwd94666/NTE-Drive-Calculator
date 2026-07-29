# 验证角色偏好弹窗的可选弧盘和暴击率输入交互。
from __future__ import annotations

import inspect
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class RoleSelectorPreferenceTests(unittest.TestCase):
    def test_optional_weapon_choice_preserves_explicit_clear(self) -> None:
        from src.features.allocation.role_selector_preferences import (
            resolve_optional_priority_choice,
        )

        self.assertEqual("", resolve_optional_priority_choice(["弧盘甲"], ""))
        self.assertEqual(
            "弧盘甲",
            resolve_optional_priority_choice(["弧盘甲"], "弧盘甲"),
        )

    def test_clearing_weapon_does_not_change_manual_crit_rate_cap(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.custom_weapons["A"] = "弧盘甲"
        selector.crit_rate_caps["A"] = 72.5

        selector._set_custom_weapon("A", "")

        self.assertNotIn("A", selector.custom_weapons)
        self.assertEqual(72.5, selector.crit_rate_caps["A"])

    def test_crit_rate_minimum_and_cap_share_one_expanding_row(self) -> None:
        from src.features.allocation.role_selector_preferences import (
            RoleSelectorPreferencesMixin,
        )

        source = inspect.getsource(
            RoleSelectorPreferencesMixin._manage_role_preferences
        )

        self.assertLess(
            source.index("crit_row.addWidget(crit_threshold_edit, 1)"),
            source.index('crit_row.addWidget(QLabel("暴击率上限："))'),
        )
        self.assertIn("crit_row.addWidget(crit_cap_edit, 1)", source)
        self.assertNotIn("cap_row", source)


if __name__ == "__main__":
    unittest.main()
