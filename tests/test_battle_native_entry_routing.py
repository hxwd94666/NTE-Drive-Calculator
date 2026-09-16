# 验证所有正式战报页面统一走原生计算，缺失或失败均不回落旧方法。
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from src.integrations.nte_analysis_core import NativeAnalysisError
from src.services.battle_report_analysis_load_service import (
    BattleReportAnalysisLoadRequest, BattleReportAnalysisLoadService,
)


class BattleNativeEntryRoutingTests(unittest.TestCase):
    def test_all_detail_levels_use_the_same_native_loader(self):
        for detail in ('overview', 'hit', 'buff', 'marginal'):
            with self.subTest(detail=detail):
                expected = object()
                native = Mock()
                native.load.return_value = expected
                history = SimpleNamespace(native_page_loader=native, load_analysis=Mock())
                request = BattleReportAnalysisLoadRequest(
                    7, detail_level=detail, start_us=100, end_us=900,
                    selected_character_id=1004, marginal_drive_units=(('CritBase', 0.032),),
                )
                progress = Mock()
                result = BattleReportAnalysisLoadService.load(history, request, progress_callback=progress)
                self.assertIs(result, expected)
                native.load.assert_called_once_with(request, progress_callback=progress)
                history.load_analysis.assert_not_called()

    def test_missing_native_loader_cannot_activate_legacy_for_any_page(self):
        for detail in ('overview', 'hit', 'buff', 'marginal'):
            with self.subTest(detail=detail):
                history = SimpleNamespace(native_page_loader=None, load_analysis=Mock())
                with self.assertRaises(NativeAnalysisError):
                    BattleReportAnalysisLoadService.load(history, BattleReportAnalysisLoadRequest(7, detail_level=detail))
                history.load_analysis.assert_not_called()

    def test_native_failure_is_returned_without_legacy_retry(self):
        native = Mock()
        native.load.side_effect = NativeAnalysisError('native failure')
        history = SimpleNamespace(native_page_loader=native, load_analysis=Mock())
        with self.assertRaisesRegex(NativeAnalysisError, 'native failure'):
            BattleReportAnalysisLoadService.load(history, BattleReportAnalysisLoadRequest(7, detail_level='marginal'))
        history.load_analysis.assert_not_called()
