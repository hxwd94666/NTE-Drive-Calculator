# 验证长战报后台请求冻结内存候选并保持主、边际分析隔离。
"""Behavior tests for the background battle-analysis load orchestration."""

from types import SimpleNamespace
from concurrent.futures import CancelledError
from threading import Event
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
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidateService,
)
from src.services.battle_analysis_progress import BattleAnalysisProgress


class BattleReportAnalysisLoadServiceTests(unittest.TestCase):
    def test_overview_skips_formula_buff_and_original_build_replays(self) -> None:
        overview = SimpleNamespace(timeline_hits=(object(),))
        history = Mock()
        history.load_analysis.return_value = overview
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

    def test_memory_candidate_compares_current_effective_build(self) -> None:
        candidate = SimpleNamespace(timeline_hits=(object(),))
        effective = SimpleNamespace(timeline_hits=(object(),))
        original = SimpleNamespace(timeline_hits=(object(),))
        projected = SimpleNamespace(timeline_hits=(object(),))
        materialized = SimpleNamespace(timeline_hits=(object(),))
        combined = SimpleNamespace(timeline_hits=(object(),))
        history = Mock()
        history.load_analysis.side_effect = (candidate, effective, original)
        history.load_target_catalog.return_value = {"kinds": ()}
        candidate_request = BattleMarginalCandidateService.freeze(
            12,
            [{"character_id": 1004}],
            equipment_editable=True,
        )
        request = BattleReportAnalysisLoadRequest(
            battle_record_id=12,
            start_us=10,
            end_us=20,
            detail_level="marginal",
            marginal_candidate=candidate_request,
        )

        with (
            patch(
                "src.services.battle_report_analysis_load_service."
                "BattleBuildCounterfactualService.compare",
                side_effect=("effective-comparison", "draft-comparison"),
            ) as compare,
            patch(
                "src.services.battle_report_analysis_load_service."
                "BattleBuildTimelineProjectionService.project",
                return_value=projected,
            ) as project,
            patch(
                "src.services.battle_report_analysis_load_service.replace",
                side_effect=(materialized, combined),
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
                    use_build_edit=False,
                    marginal_candidate=candidate_request,
                    include_buff_inference=True,
                    include_hit_replays=True,
                    include_buff_counterfactuals=True,
                ),
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
        compare.assert_has_calls([
            call(original=original, candidate=effective),
            call(original=materialized, candidate=candidate),
        ])
        project.assert_called_once_with(effective, "effective-comparison")

    def test_saved_edit_is_materialized_as_authoritative_baseline_without_draft(self) -> None:
        effective = SimpleNamespace(timeline_hits=(object(),))
        original = SimpleNamespace(timeline_hits=(object(),))
        projected = SimpleNamespace(timeline_hits=(object(),))
        materialized = SimpleNamespace(timeline_hits=(object(),))
        history = Mock()
        history.load_analysis.side_effect = (effective, original)
        history.load_target_catalog.return_value = {"kinds": ()}

        with (
            patch(
                "src.services.battle_report_analysis_load_service."
                "BattleBuildCounterfactualService.compare",
                return_value="effective-comparison",
            ) as compare,
            patch(
                "src.services.battle_report_analysis_load_service."
                "BattleBuildTimelineProjectionService.project",
                return_value=projected,
            ) as project,
            patch(
                "src.services.battle_report_analysis_load_service.replace",
                return_value=materialized,
            ) as clear_comparison,
        ):
            result = BattleReportAnalysisLoadService.load(
                history,
                BattleReportAnalysisLoadRequest(
                    battle_record_id=12,
                    detail_level="marginal",
                ),
            )

        self.assertIs(materialized, result.analysis)
        self.assertEqual(
            False,
            history.load_analysis.call_args_list[1].kwargs["use_build_edit"],
        )
        compare.assert_called_once_with(original=original, candidate=effective)
        project.assert_called_once_with(effective, "effective-comparison")
        clear_comparison.assert_called_once_with(
            projected,
            build_counterfactual=None,
        )

    def test_memory_candidate_reuses_matching_comparison_baseline(self) -> None:
        candidate = SimpleNamespace(
            timeline_hits=(object(),),
            range_start_us=1,
            range_end_us=2,
            target_condition=None,
            target_instance_resolutions=(),
            hit_replay_model_version="fixture-v1",
        )
        baseline = SimpleNamespace(
            battle_record_id=12,
            hit_replays=(object(),),
            range_start_us=1,
            range_end_us=2,
            target_condition=None,
            target_instance_resolutions=(),
            hit_replay_model_version="fixture-v1",
        )
        combined = SimpleNamespace(timeline_hits=(object(),))
        history = Mock()
        history.load_analysis.return_value = candidate
        history.load_target_catalog.return_value = {"kinds": ()}
        candidate_request = BattleMarginalCandidateService.freeze(
            12,
            [{"character_id": 1004}],
            equipment_editable=True,
        )

        with (
            patch(
                "src.services.battle_report_analysis_load_service."
                "BattleBuildCounterfactualService.compare",
                return_value=object(),
            ) as compare,
            patch(
                "src.services.battle_report_analysis_load_service.replace",
                return_value=combined,
            ),
        ):
            result = BattleReportAnalysisLoadService.load(
                history,
                BattleReportAnalysisLoadRequest(
                    battle_record_id=12,
                    detail_level="marginal",
                    marginal_candidate=candidate_request,
                    comparison_baseline=baseline,
                ),
            )

        self.assertIs(combined, result.analysis)
        self.assertEqual(1, history.load_analysis.call_count)
        compare.assert_called_once_with(original=baseline, candidate=candidate)

    def test_target_catalog_failure_keeps_completed_analysis(self) -> None:
        analysis = SimpleNamespace(timeline_hits=(object(),))
        history = Mock()
        history.load_analysis.return_value = analysis
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

    def test_target_catalog_cancellation_is_not_wrapped_as_catalog_error(self) -> None:
        analysis = SimpleNamespace(timeline_hits=(object(),))
        history = Mock()
        history.load_analysis.return_value = analysis

        def cancel(progress):
            if progress.phase == "catalog":
                raise CancelledError

        with self.assertRaises(CancelledError):
            BattleReportAnalysisLoadService.load(
                history,
                BattleReportAnalysisLoadRequest(battle_record_id=7),
                progress_callback=cancel,
            )


class _AsyncPage:
    def __init__(self, loop: QEventLoop) -> None:
        self.loop = loop
        self.loaded_ranges: list[int] = []
        self.progress_updates: list[BattleAnalysisProgress] = []

    def begin_analysis_details(self, _kind: str) -> None:
        pass

    def end_analysis_details(self) -> None:
        pass

    def set_analysis(self, analysis, *, selected_character_id=None) -> None:
        del selected_character_id
        self.loaded_ranges.append(analysis.range_start_us)
        self.loop.quit()

    def set_marginal_analysis(
        self,
        analysis,
        *,
        detail_scope=None,
        is_candidate=False,
        marginal_benefits=None,
    ) -> None:
        del detail_scope, is_candidate, marginal_benefits
        self.loaded_ranges.append(analysis.range_start_us)
        self.loop.quit()

    def set_target_catalog(self, _catalog) -> None:
        pass

    def update_analysis_progress(self, progress: BattleAnalysisProgress) -> None:
        self.progress_updates.append(progress)

    def analysis_character_id(self):
        return None

    def marginal_equipment_editable(self) -> bool:
        return True


class _AsyncHost(BattleReportAnalysisControllerMixin, QObject):
    def __init__(self, page: _AsyncPage) -> None:
        super().__init__()
        self._page = page
        self._app_context = SimpleNamespace(
            account=SimpleNamespace(active_account_id="test-account"),
            generation=3,
            paths=SimpleNamespace(static_database_path=None),
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

    @staticmethod
    def _history_operation_context():
        return SimpleNamespace(as_fields=lambda: {})

    def is_running(self) -> bool:
        return False


class BattleReportAnalysisControllerMixinTests(unittest.TestCase):
    def test_empty_production_state_rejects_deleted_record_callback(self) -> None:
        loop = QEventLoop()
        host = _AsyncHost(_AsyncPage(loop))
        request = SimpleNamespace(
            load=BattleReportAnalysisLoadRequest(battle_record_id=12),
            account_id="test-account",
            generation=3,
        )
        host._latest_state = SimpleNamespace(battle_record_id=None)

        self.assertFalse(host._analysis_request_is_current(request))

        del host._latest_state
        self.assertTrue(host._analysis_request_is_current(request))

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

        def load(_history, request, *, progress_callback=None):
            time.sleep(0.03)
            if progress_callback is not None:
                progress_callback(BattleAnalysisProgress(
                    phase=str(request.detail_scope),
                    message=str(request.detail_scope),
                    completed=1,
                    total=1,
                ))
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
        self.assertEqual(["second"], [row.phase for row in page.progress_updates])

    def test_returning_to_active_scope_reuses_running_request(self) -> None:
        app = QCoreApplication.instance() or QCoreApplication([])
        loop = QEventLoop()
        page = _AsyncPage(loop)
        host = _AsyncHost(page)
        loaded_scopes: list[str | None] = []

        def load(_history, request, *, progress_callback=None):
            loaded_scopes.append(request.detail_scope)
            time.sleep(0.03)
            return BattleReportAnalysisLoadResult(
                SimpleNamespace(
                    timeline_hits=(object(),),
                    range_start_us=1,
                    range_end_us=2,
                ),
                None,
            )

        with patch.object(BattleReportAnalysisLoadService, "load", side_effect=load):
            host._load_analysis(12, detail_scope="first")
            host._load_analysis(12, detail_scope="second")
            host._load_analysis(12, detail_scope="first")
            QTimer.singleShot(2_000, loop.quit)
            loop.exec()
            worker = host._analysis_load_worker
            if worker is not None:
                worker.wait(2_000)
            app.processEvents()

        self.assertEqual(["first"], loaded_scopes)
        self.assertEqual([1], page.loaded_ranges)

    def test_invalidated_active_request_is_not_reused(self) -> None:
        app = QCoreApplication.instance() or QCoreApplication([])
        loop = QEventLoop()
        page = _AsyncPage(loop)
        host = _AsyncHost(page)
        loaded_scopes: list[str | None] = []
        first_started = Event()
        release_first = Event()

        def load(_history, request, *, progress_callback=None):
            loaded_scopes.append(request.detail_scope)
            if len(loaded_scopes) == 1:
                first_started.set()
                release_first.wait(2.0)
            time.sleep(0.03)
            return BattleReportAnalysisLoadResult(
                SimpleNamespace(
                    timeline_hits=(object(),),
                    range_start_us=len(loaded_scopes),
                    range_end_us=len(loaded_scopes) + 1,
                ),
                None,
            )

        with patch.object(BattleReportAnalysisLoadService, "load", side_effect=load):
            host._load_analysis(12, detail_scope="first")
            self.assertTrue(first_started.wait(2.0))
            host._invalidate_analysis_loading()
            host._load_analysis(12, detail_scope="first")
            release_first.set()
            QTimer.singleShot(2_000, loop.quit)
            loop.exec()
            worker = host._analysis_load_worker
            if worker is not None:
                worker.wait(2_000)
            app.processEvents()

        self.assertEqual(["first", "first"], loaded_scopes)
        self.assertEqual([2], page.loaded_ranges)

    def test_marginal_failure_keeps_main_analysis_visible(self) -> None:
        loop = QEventLoop()
        page = _AsyncPage(loop)
        page.cleared_messages = []
        page.clear_analysis = page.cleared_messages.append
        host = _AsyncHost(page)
        request = SimpleNamespace(
            load=BattleReportAnalysisLoadRequest(
                battle_record_id=12,
                detail_level="marginal",
            ),
            account_id="test-account",
            generation=3,
        )
        host._desired_analysis_load_token = 1

        host._analysis_load_failed(1, request, "boom")

        self.assertEqual([], page.cleared_messages)
