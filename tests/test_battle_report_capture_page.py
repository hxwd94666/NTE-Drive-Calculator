# 验证战报采集工作台的单位置主按钮与重录入口。
from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from src.domain.battle_report import BattleCaptureState
from src.features.battle_report.page import BattleReportPage


class BattleReportCapturePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_capture_button_switches_action_in_place_and_shows_rerecord(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        actions = []
        page.start_requested.connect(lambda: actions.append("start"))
        page.stop_requested.connect(lambda: actions.append("stop"))

        self.assertEqual("开始采集", page.capture_button.text())
        self.assertTrue(page.rerecord_button.isHidden())
        page.capture_button.click()

        page.update_state(BattleCaptureState(
            phase="running",
            message="采集中",
            running=True,
        ))
        self.assertEqual("结束保存", page.capture_button.text())
        self.assertFalse(page.rerecord_button.isHidden())
        page.capture_button.click()

        self.assertEqual(["start", "stop"], actions)

    def test_stopping_disables_both_capture_actions(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")

        page.update_state(BattleCaptureState(
            phase="stopping",
            message="正在结束",
            running=True,
        ))

        self.assertEqual("结束保存", page.capture_button.text())
        self.assertFalse(page.capture_button.isEnabled())
        self.assertTrue(page.rerecord_button.isHidden())


if __name__ == "__main__":
    unittest.main()
