# 验证战报白色主题的可读性适配。
"""Light-theme contracts for battle-report hover and marginal selection UI."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class BattleLightThemeAdaptationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_light_tooltips_and_marginal_role_selector_use_light_surfaces(self) -> None:
        from src.app.theme import LIGHT_STYLE, apply_app_theme
        from src.features.battle_report.marginal_toolbar import build_marginal_toolbar

        apply_app_theme(self.app, "light")
        try:
            toolbar = build_marginal_toolbar(
                back=lambda: None,
                role_changed=lambda _index: None,
                inferred_toggled=lambda _checked: None,
                reset=lambda: None,
                recalculate=lambda: None,
            )
            self.assertIn(
                "QToolTip{background-color:#f6f8fa;color:#24292f",
                LIGHT_STYLE,
            )
            self.assertIn("opacity:255", LIGHT_STYLE)
            self.assertIn("background:#ddf4ff", toolbar.character_combo.parentWidget().styleSheet())
        finally:
            apply_app_theme(self.app, "black")


if __name__ == "__main__":
    unittest.main()
