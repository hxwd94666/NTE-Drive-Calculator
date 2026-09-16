# 验证无命中的换人事件仍显示在键盘轴，并且不进入输出覆盖率计数。
import unittest
from dataclasses import replace

from PySide6.QtWidgets import QApplication
from src.domain.battle_report import BattleInferredInput
from src.features.battle_report.analysis_view import BattleLongAnalysisView
from src.features.battle_report.timeline_layout import build_timeline_layout
from tests.test_battle_marginal_benefit_service import _snapshot
from tests.test_battle_timeline_layout import _action
from tests.test_battle_report_scope_ui import _hit


class TimelineSwitchEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_switches_use_time_without_requiring_a_hit_or_inflating_coverage(self):
        switches = tuple(replace(_action(i, at, at + 1), input_kind='SWITCH',
                                 hits=0, damage=0, evidence_event_ids=(f'context:{i}',))
                         for i, at in ((1, 999), (2, 1000), (3, 1999), (4, 2000)))
        missed = replace(_action(5, 1500, 1600), hits=0, damage=0, evidence_event_ids=('context:5',))
        attack = replace(_action(6, 1400, 1600), evidence_event_ids=('hit',))
        inputs = tuple(BattleInferredInput(f'input:{a.action_id}', a.action_id, 'keyboard',
                        '' if a.input_kind == 'SWITCH' else 'E', a.character_id, a.character_name,
                        a.start_us, a.start_us + 1, a.input_kind == 'SWITCH', '高')
                       for a in (*switches, missed, attack))
        hit = _hit('hit', 1500, 100)
        analysis = replace(_snapshot(), range_start_us=1000, range_end_us=2000,
                           timeline_hits=(hit,), hits=(hit,), inferred_actions=(*switches, missed, attack),
                           inferred_inputs=inputs, timeline_damage_groups=())
        view = BattleLongAnalysisView()
        try:
            view._analysis = analysis
            view.timeline.set_analysis(analysis)
            visible = view.timeline._visible_analysis()
            selected = view._selected_axis_evidence()
            self.assertEqual(selected, (visible.timeline_hits, visible.inferred_actions, visible.inferred_inputs))
            self.assertEqual([switches[1].action_id, switches[2].action_id, attack.action_id],
                             [a.action_id for a in visible.inferred_actions])
            layout = build_timeline_layout(visible, x_for_time=float)
            avatars = [(lane, item) for lane, item, _ in layout.input_rows if item.is_character_switch]
            self.assertEqual(len(avatars), 2)
            self.assertTrue(all(lane.key == 'input:keyboard' for lane, _ in avatars))
            view._render_time_presentation()
            self.assertIn('引用出伤事件 1/1（100.0%）', view.action_summary_label.text())
            self.assertEqual(len(analysis.inferred_actions), 6)
        finally:
            view.close()
