# 验证瞬时输入条按可见区域选择，粘性标签遮挡不会误选事件。
import unittest
from types import SimpleNamespace
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import QApplication
from src.features.battle_report.analysis.view import BattleLongAnalysisView
from src.features.battle_report.timeline.layout import TimelineLane, TimelinePaintedBar


class BattleTimelineInteractionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_instant_input_bar_remains_selectable_except_under_sticky_labels(self) -> None:
        view = BattleLongAnalysisView()
        self.addCleanup(view.close)
        timeline = view.timeline
        timeline.resize(980, 300)
        timeline._analysis = SimpleNamespace(
            battle_start_us=0,
            battle_end_us=100_000_000,
            timeline_end_us=100_000_000,
            time_stop_intervals=(),
        )
        lane = TimelineLane(
            key="input:keyboard",
            label="推算键盘",
            kind="input",
            top=42,
            height=31,
        )
        rect = QRectF(300, 47, 52, 21)
        event_time = timeline._display_time_for_x(rect.left())
        timeline._lanes = (lane,)
        timeline._painted_hits = []
        timeline._painted_bars = [
            TimelinePaintedBar(
                kind="input",
                item_id="input:g",
                action_id="action:g",
                lane_key=lane.key,
                rect=rect,
                start_us=event_time,
                end_us=event_time + 1,
                payload=SimpleNamespace(start_us=event_time),
            )
        ]
        point = QPointF(rect.right() - 1, rect.center().y())

        self.assertGreater(
            timeline._display_time_for_x(point.x()),
            event_time + 1,
        )
        candidates = timeline._selection_candidates(point)

        self.assertEqual(("input:g",), tuple(item.item_id for item in candidates))

        timeline.set_horizontal_view_offset(240)
        self.assertEqual([], timeline._selection_candidates(point))
