# 验证长战报后台请求只为生效副本计算逐 Buff 反事实。
"""Behavior tests for the background battle-analysis load orchestration."""

from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, call, patch

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QTimer

from src.features.battle_report.analysis_controller_mixin import (
    BattleReportAnalysisControllerMixin,
)
from src.services.battle_report_analysis_load_service import (
    BattleReportAnalysisLoadRequest,
    BattleReportAnalysisLoadResult,
    BattleReportAnalysisLoadService,
)


class BattleReportAnalysisLoadServiceTests(unittest.TestCase):
    def test_overview_skips_formula_buff_and_original_build_replays(self) -> None:
        overview = SimpleNamespace(timeline_hits=(object(),))
        history = Mock()
        history.load_analysis.return_value = overview
        history.load_build_edit_state.return_value = {"is_active": True}
        history.load_target_catalog.return_value = {"kinds": ()}

        result = BattleReportAnalysisLoadService.load(
            history,
            BattleReportAnalysisLoadRequest(
                battle_record_id=12,
                detail_scope="first",
            ),
        )

        self.assertIs(overview, result.analysis)
        history.load_analysis.assert_called_once_with(
            12,
            start_us=None,
            end_us=None,
            detail_scope="first",
            include_buff_inference=False,
            include_hit_replays=False,
            include_buff_counterfactuals=False,
        )

    def test_active_edit_skips_original_buff_counterfactuals(self) -> None:
        candidate = SimpleNamespace(timeline_hits=(object(),))
        original = SimpleNamespace(timeline_hits=(object(),))
        combined = SimpleNamespace(timeline_hits=(object(),))
        history = Mock()
        history.load_analysis.side_effect = (candidate, original)
        history.load_build_edit_state.return_value = {"is_active": True}
        history.load_target_catalog.return_value = {"kinds": ()}
        request = BattleReportAnalysisLoadRequest(
            battle_record_id=12,
            start_us=10,
            end_us=20,
            detail_level="marginal",
        )

        with (
            patch(
                "src.services.battle_report_analysis_load_service."
                "BattleBuildCounterfactualService.compare",
                return_value=object(),
            ),
            patch(
                "src.services.battle_report_analysis_load_service.replace",
                return_value=combined,
            ),
        ):
            result = BattleReportAnalysisLoadService.load(history, request)

        self.assertIs(combined, result.analysis)
        self.assertEqual({"kinds": ()}, result.target_catalog)
        self.assertEqual(
            [
                call(
                    12,
                    start_us=10,
                    end_us=20,
                    detail_scope=None,
                    include_buff_inference=True,
                    include_hit_replays=True,
                    include_buff_counterfactuals=False,
                ),
                call(
                    12,
                    start_us=10,
                    end_us=20,
                    detail_scope=None,
                    use_build_edit=False,
                    include_buff_inference=True,
                    include_hit_replays=True,
                    include_buff_counterfactuals=False,
                ),
            ],
            history.load_analysis.call_args_list,
        )

    def test_target_catalog_failure_keeps_completed_analysis(self) -> None:
        analysis = SimpleNamespace(timeline_hits=(object(),))
        history = Mock()
        history.load_analysis.return_value = analysis
        history.load_build_edit_state.return_value = {"is_active": False}
        history.load_target_catalog.side_effect = RuntimeError(
            "catalog unavailable"
        )

        result = BattleReportAnalysisLoadService.load(
            history,
            BattleReportAnalysisLoadRequest(
                battle_record_id=7,
                detail_level="hit",
            ),
        )

        self.assertIs(analysis, result.analysis)
        self.assertIsNone(result.target_catalog)
        self.assertIsInstance(result.target_catalog_error, RuntimeError)


class _AsyncPage:
    def __init__(self, loop: QEventLoop) -> None:
        self.loop = loop
        self.loaded_ranges: list[int] = []

    def begin_analysis_details(self, _kind: str) -> None:
        pass

    def end_analysis_details(self) -> None:
        pass

    def set_analysis(self, analysis, *, selected_character_id=None) -> None:
        del selected_character_id
        self.loaded_ranges.append(analysis.range_start_us)
        self.loop.quit()

    def set_target_catalog(self, _catalog) -> None:
        pass

    def analysis_character_id(self):
        return None


class _AsyncHost(BattleReportAnalysisControllerMixin, QObject):
    def __init__(self, page: _AsyncPage) -> None:
        super().__init__()
        self._page = page
        self._app_context = SimpleNamespace(
            account=SimpleNamespace(active_account_id="test-account"),
            generation=3,
        )
        self._closing = False
        self._build_snapshot_controller = SimpleNamespace(
            refresh=lambda _record_id: None
        )
        self._history = Mock()
        self._initialize_analysis_loading()

    def _current_history_service(self):
        return self._history

    def _save_analysis_range(self, *_args) -> None:
        pass

    def is_running(self) -> bool:
        return False


class BattleReportAnalysisControllerMixinTests(unittest.TestCase):
    def test_composition_detail_reuses_hit_replay_load(self) -> None:
        loop = QEventLoop()
        page = _AsyncPage(loop)
        host = _AsyncHost(page)
        host._latest_state = SimpleNamespace(battle_record_id=12)
        host._latest_analysis_load_request = BattleReportAnalysisLoadRequest(
            battle_record_id=12,
            start_us=10,
            end_us=20,
            detail_scope="first",
        )

        with patch.object(host, "_load_analysis") as load:
            host._load_analysis_details("composition")

        load.assert_called_once_with(
            12,
            start_us=10,
            end_us=20,
            selected_character_id=None,
            detail_scope="first",
            detail_level="hit",
            completion_kind="composition",
            completion_payload=None,
        )

    def test_latest_scope_replaces_a_running_stale_request(self) -> None:
        app = QCoreApplication.instance() or QCoreApplication([])
        loop = QEventLoop()
        page = _AsyncPage(loop)
        host = _AsyncHost(page)

        def load(_history, request):
            time.sleep(0.03)
            start = 1 if request.detail_scope == "first" else 2
            analysis = SimpleNamespace(
                timeline_hits=(object(),),
                range_start_us=start,
                range_end_us=start + 1,
            )
            return BattleReportAnalysisLoadResult(analysis, None)

        with patch.object(BattleReportAnalysisLoadService, "load", side_effect=load):
            host._load_analysis(12, detail_scope="first")
            host._load_analysis(12, detail_scope="second")
            QTimer.singleShot(2_000, loop.quit)
            loop.exec()
            worker = host._analysis_load_worker
            if worker is not None:
                worker.wait(2_000)
            app.processEvents()

        self.assertEqual([2], page.loaded_ranges)
