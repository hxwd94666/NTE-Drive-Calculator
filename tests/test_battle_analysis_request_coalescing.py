# 验证逐击展示选择合并在途计算，同时保持真正计算输入和取消边界。
from dataclasses import replace
from concurrent.futures import CancelledError
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from src.features.battle_report.analysis.loading import (
    _AnalysisPresentationRequest, _same_analysis_request,
)
from src.domain.battle_report.analysis.page_results import BattleReportAnalysisLoadResult
from src.services.battle_report.analysis.load import BattleReportAnalysisLoadRequest, BattleReportAnalysisLoadService
from tests.battle_report.analysis.test_load_lifecycle import _AsyncHost, _AsyncPage


class AnalysisRequestCoalescingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_latest_hit_selection_consumes_one_running_load_without_progress_reset(self):
        loop = QEventLoop()
        page = _AsyncPage(loop)
        page.begin_analysis_details = Mock()
        page.complete_analysis_details = Mock()
        page.set_analysis = Mock(side_effect=lambda *a, **kw: loop.quit())
        host = _AsyncHost(page)
        started, release = Event(), Event()

        def load(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(2))
            return BattleReportAnalysisLoadResult(SimpleNamespace(
                timeline_hits=(object(),), range_start_us=0, range_end_us=10,
            ), None)

        try:
            with patch.object(BattleReportAnalysisLoadService, 'load', side_effect=load) as compute:
                host._load_analysis(12, detail_level='hit', selected_character_id=1003,
                                    completion_kind='hit', completion_payload=1)
                self.assertTrue(started.wait(2))
                token = host._desired_analysis_load_token
                host._load_analysis(12, detail_level='hit', selected_character_id=1004,
                                    completion_kind='hit', completion_payload=2)
                self.assertEqual(token, host._desired_analysis_load_token)
                self.assertFalse(host._active_analysis_load_invalidated)
                release.set()
                QTimer.singleShot(2000, loop.quit)
                loop.exec()
                if host._analysis_load_worker is not None:
                    self.assertTrue(host._analysis_load_worker.wait(2000))
                self.app.processEvents()
                compute.assert_called_once()
            page.begin_analysis_details.assert_called_once_with('hit')
            page.complete_analysis_details.assert_called_once_with('hit', 2)
            self.assertEqual(1004, page.set_analysis.call_args.kwargs['selected_character_id'])
        finally:
            release.set()
            if host._analysis_load_worker is not None:
                host._analysis_load_worker.wait(2000)
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_computational_changes_remain_distinct(self):
        original = _AnalysisPresentationRequest(
            BattleReportAnalysisLoadRequest(12, detail_level='marginal', selected_character_id=1003),
            'account', 1,
        )
        for field, value in [('selected_character_id', 1004), ('detail_scope', 'first'),
                             ('start_us', 10), ('battle_record_id', 13),
                             ('marginal_drive_units', (('CritBase', 0.04),))]:
            with self.subTest(field=field):
                self.assertFalse(_same_analysis_request(original, replace(
                    original, load=replace(original.load, **{field: value}))))
        self.assertFalse(_same_analysis_request(original, replace(original, generation=2)))
        self.assertFalse(_same_analysis_request(original, replace(original, account_id='other')))
        self.assertFalse(_same_analysis_request(original, replace(original, load=replace(
            original.load, comparison_baseline=object()))))

    def test_successful_fact_change_replaces_identical_inflight_selectors(self):
        loop = QEventLoop()
        page = _AsyncPage(loop)
        host = _AsyncHost(page)
        started, release = Event(), Event()
        calls = []

        def load(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                started.set()
                release.wait(2)
            return BattleReportAnalysisLoadResult(SimpleNamespace(
                timeline_hits=(object(),), range_start_us=len(calls), range_end_us=10,
            ), None)

        try:
            with patch.object(BattleReportAnalysisLoadService, 'load', side_effect=load):
                host._load_analysis(12)
                self.assertTrue(started.wait(2))
                host._load_changed_analysis(12)
                self.assertTrue(host._active_analysis_load_invalidated)
                release.set()
                QTimer.singleShot(2000, loop.quit)
                loop.exec()
                if host._analysis_load_worker is not None:
                    host._analysis_load_worker.wait(2000)
                self.app.processEvents()
            self.assertEqual(2, len(calls))
            self.assertEqual([2], page.loaded_ranges)
        finally:
            release.set()
            if host._analysis_load_worker is not None:
                host._analysis_load_worker.wait(2000)
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_internal_cancellation_finishes_progress_without_result(self):
        loop = QEventLoop()
        page = _AsyncPage(loop)
        page.end_analysis_details = Mock(side_effect=loop.quit)
        host = _AsyncHost(page)
        try:
            with patch.object(BattleReportAnalysisLoadService, 'load', side_effect=CancelledError):
                host._load_analysis(12, detail_level='hit')
                QTimer.singleShot(2000, loop.quit)
                loop.exec()
                self.app.processEvents()
            self.assertIsNone(host._analysis_load_worker)
            page.end_analysis_details.assert_called_once()
            self.assertEqual([], page.loaded_ranges)
        finally:
            if host._analysis_load_worker is not None:
                host._analysis_load_worker.wait(2000)
            host.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
