# 验证边际时间轴上所有可见逐击都能打开公式详情。
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from src.features.battle_report.marginal_page import BattleMarginalPage
from src.features.battle_report.timeline_layout import TimelineSelection


class BattleMarginalTimelineDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _hit() -> SimpleNamespace:
        return SimpleNamespace(
            event_id="24-primary",
            relative_time_us=3_616_106,
            character_id=1075,
        )

    def _page(self, comparison) -> tuple[BattleMarginalPage, object, object]:
        page = BattleMarginalPage()
        hit = self._hit()
        replay = SimpleNamespace(event_id=hit.event_id)
        analysis = SimpleNamespace(
            build_counterfactual=comparison,
            hits=(hit,),
            timeline_hits=(hit,),
            hit_replays=(replay,),
            buff_intervals=(),
        )
        page._analysis = analysis
        page._candidate_analysis = analysis
        page._counterfactual_hit_dialog = Mock()
        return page, hit, replay

    def test_visible_hit_opens_original_formula_before_recalculation(self) -> None:
        page, hit, replay = self._page(None)

        page._open_counterfactual_hit(
            TimelineSelection("hit", hit.event_id, hit)
        )

        page._counterfactual_hit_dialog.show_for_hit.assert_called_once_with(
            hit,
            replay,
            active_buffs=(),
            counterfactual=None,
            related_counterfactuals=(),
            related_analysis=page._candidate_analysis,
        )

    def test_missing_candidate_row_does_not_make_visible_hit_inert(self) -> None:
        comparison = SimpleNamespace(hits=())
        page, hit, replay = self._page(comparison)

        page._open_counterfactual_hit(
            TimelineSelection("hit", hit.event_id, hit)
        )

        call = page._counterfactual_hit_dialog.show_for_hit.call_args
        self.assertIs(hit, call.args[0])
        self.assertIs(replay, call.args[1])
        self.assertIsNone(call.kwargs["counterfactual"])


if __name__ == "__main__":
    unittest.main()
