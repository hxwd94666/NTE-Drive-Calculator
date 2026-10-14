# 验证战报采集操作随运行和结束状态切换，结束中不重复保存或丢弃。
from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from src.domain.battle_report.capture.state import BattleCaptureState
from src.features.battle_report.page import BattleReportPage


class BattleReportCapturePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_capture_actions_follow_running_and_stopping_state(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        self.addCleanup(page.close)
        actions = []
        page.start_requested.connect(lambda: actions.append("start"))
        page.stop_requested.connect(lambda: actions.append("stop"))
        page.rerecord_requested.connect(lambda: actions.append("rerecord"))

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
        page.rerecord_button.click()
        page.capture_button.click()

        self.assertEqual(["start", "rerecord", "stop"], actions)
        # 同一页面进入结束阶段：禁用须阻止真实点击信号，不只隐藏重录按钮。
        page.update_state(BattleCaptureState(
            phase="stopping",
            message="正在结束",
            running=True,
        ))

        self.assertFalse(page.capture_button.isEnabled())
        self.assertTrue(page.rerecord_button.isHidden())
        self.assertFalse(page.rerecord_button.isEnabled())
        page.capture_button.click()
        page.rerecord_button.click()
        self.assertEqual(["start", "rerecord", "stop"], actions)


if __name__ == "__main__":
    unittest.main()
