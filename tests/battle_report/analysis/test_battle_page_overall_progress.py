# 验证整场百分比按真实阶段和完成数量推进，失败不满格且新请求不串进度。
from dataclasses import replace
import unittest

from src.services.battle_report.analysis.progress import BattleAnalysisProgress
from src.services.battle_report.analysis.progress import BattlePageOverallProgress
from src.services.battle_report.analysis.load import BattleReportAnalysisLoadRequest


class BattlePageOverallProgressTests(unittest.TestCase):
    def marginal(self):
        return BattleReportAnalysisLoadRequest(68, detail_level='marginal',
            selected_character_id=1003, marginal_benefit_candidate=object(), marginal_drive_units=())

    def test_whole_page_is_monotonic_across_repeated_analysis_and_stage_counts(self):
        tracker = BattlePageOverallProgress(self.marginal())
        values = []
        for phase, done, total in [
            ('native_page', None, None), ('load', None, None), ('target', None, None),
            ('analyze', None, None), ('buff_remove', 0, 27), ('buff_remove', 14, 27),
            ('buff_remove', 27, 27), ('analyze', None, None), ('core_baseline', None, None),
            ('core_candidates', 0, 15), ('core_candidates', 7, 15), ('core_candidates', 15, 15),
            ('fork', None, None), ('panel', None, None), ('details', None, None),
            ('serialize', None, None), ('decode', None, None),
        ]:
            event = tracker.project(BattleAnalysisProgress(phase, phase, done, total))
            values.append(event.overall_percent)
            self.assertEqual((event.completed, event.total), (done, total))
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[0], 1)
        self.assertLess(max(values), 100)
        self.assertGreater(values[5], values[4])
        self.assertGreater(values[10], values[9])
        self.assertEqual(tracker.project(BattleAnalysisProgress('complete', '完成')).overall_percent, 100)

    def test_unknown_or_duplicate_events_do_not_advance_and_new_request_starts_at_one(self):
        tracker = BattlePageOverallProgress(self.marginal())
        event = BattleAnalysisProgress('buff_remove', 'Buff', 7, 27)
        value = tracker.project(event).overall_percent
        for duplicate in (event, replace(event, completed=3), BattleAnalysisProgress('unknown', '未知')):
            self.assertEqual(tracker.project(duplicate).overall_percent, value)
        fresh = BattlePageOverallProgress(self.marginal())
        self.assertEqual(fresh.project(BattleAnalysisProgress('native_page', '开始')).overall_percent, 1)

if __name__ == '__main__':
    unittest.main()
