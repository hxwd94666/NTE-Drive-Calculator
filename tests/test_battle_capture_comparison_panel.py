# 验证双路采集对照展示计时差异、当前配对、异常和切换清理。
from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from src.domain.battle_capture_comparison import BattleCaptureComparisonState
from src.domain.battle_report import (
    BattleAbyssSummary, BattleCaptureState, BattleQualitySummary, BattleSummary,
)
from src.features.battle_report.comparison_panel import BattleCaptureComparisonPanel
from src.features.battle_report.page import BattleReportPage


def _state(
    damage: float | None,
    *,
    clock: str = "wall_clock",
    phase: str = "running",
    record_id: int | None = None,
    error: str | None = None,
) -> BattleCaptureState:
    summary = None if damage is None else BattleSummary(
        duration_seconds=10, dps_time_mode=clock, total_damage=damage,
        total_dps=damage / 10, total_damage_taken=30, total_hits=12,
        characters=(), skills=(), abyss=BattleAbyssSummary(), quality=BattleQualitySummary(),
    )
    return BattleCaptureState(
        phase=phase, message="当前采集", running=phase == "running",
        summary=summary, battle_record_id=record_id, error=error,
    )


class BattleCaptureComparisonPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def panel(self) -> BattleCaptureComparisonPanel:
        panel = BattleCaptureComparisonPanel()
        self.addCleanup(panel.close)
        return panel

    def test_different_clocks_keep_both_values_without_comparing_dps(self) -> None:
        panel = self.panel()
        panel.set_snapshot(BattleCaptureComparisonState(
            native=_state(1200), packet=_state(1000, clock="subtract_time_stop"),
        ))
        self.assertEqual(panel.values["native"]["damage"].text(), "1,200")
        self.assertEqual(panel.values["packet"]["damage"].text(), "1,000")
        self.assertEqual(panel.values["native"]["dps"].text(), "120.00")
        self.assertEqual(panel.values["packet"]["dps"].text(), "100.00")
        self.assertIn("真实经过", panel.values["native"]["clock"].text())
        self.assertIn("扣除停表", panel.values["packet"]["clock"].text())
        self.assertIn("计时方式不同", panel.dps_difference.text())
        self.assertIn("200", panel.damage_difference.text())
        self.assertIn("120.00%", panel.damage_ratio.text())

    def test_same_clock_comparison_has_explicit_direction_and_denominator(self) -> None:
        panel = self.panel()
        panel.set_snapshot(BattleCaptureComparisonState(
            native=_state(1200, phase="stopped", record_id=41),
            packet=_state(1000, phase="stopped", record_id=42), finished=True,
        ))
        self.assertIn("DLL − 抓包", panel.dps_difference.text())
        self.assertIn("20.00", panel.dps_difference.text())
        self.assertEqual(panel.values["native"]["record_id"].text(), "41")
        self.assertEqual(panel.values["packet"]["record_id"].text(), "42")
        self.assertIn("不代表来源覆盖完整", panel.notice.text())

    def test_missing_or_unready_source_does_not_become_zero(self) -> None:
        panel = self.panel()
        panel.set_snapshot(BattleCaptureComparisonState(
            native=_state(1200), packet=_state(None, phase="starting"),
        ))
        self.assertEqual(panel.values["packet"]["damage"].text(), "—")
        self.assertIn("等待双方", panel.dps_difference.text())
        panel.set_snapshot(BattleCaptureComparisonState(
            native=_state(1200), packet=_state(1000, phase="starting"),
        ))
        self.assertIn("等待双方采集就绪", panel.dps_difference.text())
        panel.set_snapshot(BattleCaptureComparisonState(native=_state(1200), packet=_state(0)))
        self.assertIn("未定义", panel.damage_ratio.text())

    def test_interrupted_pair_keeps_observations_and_clear_removes_old_ids(self) -> None:
        panel = self.panel()
        panel.set_snapshot(BattleCaptureComparisonState(
            native=_state(1200, record_id=41),
            packet=_state(1000, phase="error", error="设备断开", record_id=42),
            interrupted=True, finished=True,
        ))
        self.assertIn("中断", panel.notice.text())
        self.assertIn("设备断开", panel.values["packet"]["status"].text())
        self.assertIn("保留双方实际值", panel.dps_difference.text())
        panel.set_snapshot(None)
        self.assertTrue(panel.isHidden())
        self.assertEqual(panel.values["native"]["record_id"].text(), "")
        self.assertEqual(panel.values["packet"]["record_id"].text(), "")

    def test_page_toggle_is_optional_and_locked_until_pair_finishes(self) -> None:
        page = BattleReportPage(game_ui_asset_root="data/game_ui")
        self.addCleanup(page.close)
        self.assertFalse(page.comparison_toggle.isChecked())
        page.update_state(_state(None, phase="starting"))
        self.assertFalse(page.comparison_toggle.isEnabled())
        page.set_capture_comparison(None)
        self.assertFalse(page.comparison_toggle.isEnabled())
        page.set_capture_comparison(BattleCaptureComparisonState(
            native=_state(1200, phase="stopped"), packet=_state(1000),
        ))
        page.update_state(_state(1200, phase="stopped"))
        self.assertFalse(page.comparison_toggle.isEnabled())
        page.set_capture_comparison(BattleCaptureComparisonState(
            native=_state(1200, phase="stopped"), packet=_state(1000, phase="stopped"), finished=True,
        ))
        self.assertTrue(page.comparison_toggle.isEnabled())
        page.update_state(_state(100, phase="history"))
        self.assertTrue(page.comparison_panel.isHidden())
        page.set_capture_comparison(BattleCaptureComparisonState(native=_state(10), packet=_state(20)))
        page.clear_summary()
        self.assertTrue(page.comparison_panel.isHidden())


if __name__ == "__main__":
    unittest.main()
