# 验证长战报后台请求冻结内存候选并保持主、边际分析隔离。
"""Behavior tests for the background battle-analysis load orchestration."""

from types import SimpleNamespace
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QObject, QTimer

from src.features.battle_report.analysis_controller_mixin import (
    BattleReportAnalysisControllerMixin,
)
from src.domain.battle_page_models import BattleReportAnalysisLoadResult
from src.services.battle_report.analysis.load import BattleReportAnalysisLoadRequest, BattleReportAnalysisLoadService
from src.services.battle_report.analysis.progress import BattleAnalysisProgress


class _AsyncPage:
    def __init__(self, loop: QEventLoop) -> None:
        self.loop = loop
        self.loaded_ranges: list[int] = []
        self.progress_updates: list[BattleAnalysisProgress] = []

    def begin_analysis_details(self, _kind: str) -> None:
        pass

    def end_analysis_details(self) -> None:
        pass

    def set_analysis(self, analysis, *, selected_character_id=None, hit_details=None) -> None:
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
        marginal_panel=None, candidate_display_analysis=None, hit_details=None,
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
        self._history = Mock(native_page_loader=None)
        self._marginal_units_provider = lambda: {"CritBase": 0.032}
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
    def setUp(self) -> None:
        self._hosts: list[_AsyncHost] = []

    def _host(self, page: _AsyncPage) -> _AsyncHost:
        host = _AsyncHost(page)
        self._hosts.append(host)
        return host

    def tearDown(self) -> None:
        # Qt owner 必须在主线程销毁，避免循环引用被后续 worker 的 GC 回收。
        for host in self._hosts:
            worker = host._analysis_load_worker
            if worker is not None:
                self.assertTrue(worker.wait(2_000))
            host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_empty_production_state_rejects_deleted_record_callback(self) -> None:
        loop = QEventLoop()
        host = self._host(_AsyncPage(loop))
        request = SimpleNamespace(
            load=BattleReportAnalysisLoadRequest(battle_record_id=12),
            account_id="test-account",
            generation=3,
        )
        host._latest_state = SimpleNamespace(battle_record_id=None)

        self.assertFalse(host._analysis_request_is_current(request))

        del host._latest_state
        self.assertTrue(host._analysis_request_is_current(request))

    def test_composition_detail_requests_only_topple_replay(self) -> None:
        loop = QEventLoop()
        page = _AsyncPage(loop)
        host = self._host(page)
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
            detail_level="composition",
            completion_kind="composition",
            completion_payload=None,
        )

    def test_latest_scope_replaces_a_running_stale_request(self) -> None:
        app = QCoreApplication.instance() or QCoreApplication([])
        loop = QEventLoop()
        page = _AsyncPage(loop)
        host = self._host(page)

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
        host = self._host(page)
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
        host = self._host(page)
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

    def test_half_hit_and_buff_failures_keep_existing_report_visible(self) -> None:
        # 同一原报告保留边界覆盖各真实失败路由；倾陷原因与边际回调不合并成泛化错误。
        cases = [(half, detail, "详情不可用", None)
                 for half in ("first", "second") for detail in ("hit", "buff")]
        cases.extend([(None, "marginal", "boom", "boom"),
                      (None, "composition", "组件能力缺失", "倾陷归属未完成：组件能力缺失")])
        for half, detail_level, error, expected in cases:
            with self.subTest(half=half, detail_level=detail_level):
                page = _AsyncPage(QEventLoop())
                page.clear_analysis = Mock()
                page.show_analysis_detail_error = Mock()
                page.show_marginal_error = Mock()
                host = self._host(page)
                request = SimpleNamespace(
                    load=BattleReportAnalysisLoadRequest(
                        battle_record_id=12, detail_level=detail_level, detail_scope=half),
                    account_id="test-account", generation=3,
                )
                host._desired_analysis_load_token = 1
                host._analysis_load_failed(1, request, error)
                page.clear_analysis.assert_not_called()
                if detail_level == "marginal":
                    page.show_marginal_error.assert_called_once_with(expected)
                    page.show_analysis_detail_error.assert_not_called()
                else:
                    page.show_marginal_error.assert_not_called()
                    if expected is None:
                        page.show_analysis_detail_error.assert_called_once()
                    else:
                        page.show_analysis_detail_error.assert_called_once_with(expected)
                if detail_level in ("hit", "buff"):
                    page.show_analysis_detail_error.reset_mock()
                    host._analysis_load_ready(1, request, BattleReportAnalysisLoadResult(None, None))
                    page.clear_analysis.assert_not_called()
                    page.show_analysis_detail_error.assert_called_once()


# 改名后仍属于核心迟到回调与账号/取消保护，不依赖旧文件名命中。
NTE_TEST_TIER = "core"
