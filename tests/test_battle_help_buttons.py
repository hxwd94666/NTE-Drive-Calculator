# 验证战报问号统一复用计算页的 btnHelp 主题契约。
from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QPushButton

from src.features.battle_report.analysis_view import BattleLongAnalysisView
from src.features.battle_report.marginal_page import BattleMarginalPage
from src.features.battle_report.page import BattleReportPage


class BattleHelpButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_all_battle_question_buttons_use_shared_help_style(self) -> None:
        widgets = (
            BattleLongAnalysisView(),
            BattleMarginalPage(),
            BattleReportPage(game_ui_asset_root=None),
        )
        try:
            buttons = tuple(
                button
                for widget in widgets
                for button in widget.findChildren(QPushButton)
                if button.text() == "?"
            )
            self.assertGreaterEqual(len(buttons), 4)
            self.assertTrue(all(button.objectName() == "btnHelp" for button in buttons))
            self.assertTrue(all(button.toolTip() for button in buttons))
        finally:
            for widget in widgets:
                widget.close()


if __name__ == "__main__":
    unittest.main()
