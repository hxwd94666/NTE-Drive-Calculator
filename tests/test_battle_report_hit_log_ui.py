# 验证逐击日志按需加载公式重放并统一展示有符号误差。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from src.domain.battle_report import BattleAnalysisHit, BattleHitReplayResult
from src.features.battle_report.analysis_view import BattleLongAnalysisView


class BattleReportHitLogUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_hit_log_requests_replay_then_opens_with_signed_error(self) -> None:
        view = BattleLongAnalysisView()
        hit = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=1004,
            character_name="安魂曲",
            skill_name="安魂曲的噩梦",
            damage_name="安魂曲的噩梦",
            damage_component="dot",
            attack_type="Dot Damage",
            damage_attribute="COSMOS",
            target_id="boss",
            target_name="墨菲克斯",
            damage=24.0,
            direction="outgoing",
            is_follow_up=False,
            classification="dot",
        )
        requested = []
        view.details_requested.connect(
            lambda kind, payload: requested.append((kind, payload))
        )
        view._analysis = SimpleNamespace(
            hits=(hit,),
            hit_replays=(),
            battle_start_us=0,
            time_stop_intervals=(),
            buff_intervals=(),
        )

        view.audit_buttons["hits"].click()

        self.assertEqual([("hit", None)], requested)
        self.assertFalse(view.log_dialog.isVisible())

        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=24.0,
            non_critical_damage=24.0,
            critical_damage=None,
            selected_damage=22.0,
            selected_error_percent=8.3333333333,
            critical_state="non_critical",
            confidence="高",
            factors=(),
            formula_type="持续伤害",
            signed_error_percent=-8.3333333333,
        )
        view._analysis = SimpleNamespace(
            hits=(hit,),
            hit_replays=(replay,),
            battle_start_us=0,
            time_stop_intervals=(),
            buff_intervals=(),
        )

        view.complete_analysis_details("hit", None)

        self.assertTrue(view.log_dialog.isVisible())
        self.assertEqual("22 / -8.33%", view.log_table.item(0, 7).text())
        view.log_dialog.hide()


if __name__ == "__main__":
    unittest.main()
