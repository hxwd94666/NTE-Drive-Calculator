# 将长战报分析放入可合并的后台任务，避免阻塞 Qt 事件循环。
"""Asynchronous battle-analysis loading owned by the report controller."""

from __future__ import annotations

from dataclasses import dataclass

from src.app.workers import WorkerThread
from src.observability.operation import log_event
from src.observability.redaction import safe_exception
from src.services.battle_report_analysis_load_service import (
    BattleReportAnalysisLoadRequest,
    BattleReportAnalysisLoadResult,
    BattleReportAnalysisLoadService,
)
from src.services.battle_report_history_service import (
    BattleReportHistoryService,
    StaleBattleReportContextError,
)
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidate,
    BattleMarginalCandidateService,
)


@dataclass(frozen=True, slots=True)
class _AnalysisPresentationRequest:
    load: BattleReportAnalysisLoadRequest
    account_id: str
    generation: int
    persist_full_range: bool = False
    selected_character_id: int | None = None
    completion_kind: str = ""
    completion_payload: object | None = None


class BattleReportAnalysisControllerMixin:
    """Coalesce long-page requests and discard results from stale contexts."""

    def _initialize_analysis_loading(self) -> None:
        self._analysis_load_token = 0
        self._analysis_load_worker: WorkerThread | None = None
        self._latest_analysis_load_request: BattleReportAnalysisLoadRequest | None = None
        self._pending_analysis_load: tuple[
            int,
            _AnalysisPresentationRequest,
            BattleReportHistoryService,
        ] | None = None

    def _invalidate_analysis_loading(self) -> None:
        self._analysis_load_token += 1
        self._pending_analysis_load = None
        end_details = getattr(self._page, "end_analysis_details", None)
        if callable(end_details):
            end_details()

    def _load_analysis_range(self, start_us: int, end_us: int) -> None:
        record_id = self._latest_state.battle_record_id
        if record_id is None or self.is_running():
            return
        self._load_analysis(record_id, start_us=start_us, end_us=end_us)

    def _reset_analysis_range(self) -> None:
        record_id = self._latest_state.battle_record_id
        if record_id is None or self.is_running():
            return
        self._load_analysis(record_id, detail_scope=self._page.detail_scope())

    def _load_analysis(
        self,
        battle_record_id: int,
        *,
        start_us: int | None = None,
        end_us: int | None = None,
        persist_full_range: bool = False,
        selected_character_id: int | None = None,
        detail_scope: str | None = None,
        detail_level: str = "overview",
        marginal_candidate: BattleMarginalCandidate | None = None,
        completion_kind: str = "",
        completion_payload: object | None = None,
    ) -> None:
        if detail_level == "overview":
            end_details = getattr(self._page, "end_analysis_details", None)
            if callable(end_details):
                end_details()
        else:
            self._page.begin_analysis_details(
                completion_kind or detail_level
            )
        history = self._current_history_service()
        account = self._app_context.account
        presentation = _AnalysisPresentationRequest(
            load=BattleReportAnalysisLoadRequest(
                battle_record_id=battle_record_id,
                start_us=start_us,
                end_us=end_us,
                detail_scope=detail_scope,
                detail_level=detail_level,
                marginal_candidate=marginal_candidate,
            ),
            account_id=account.active_account_id,
            generation=self._app_context.generation,
            persist_full_range=persist_full_range,
            selected_character_id=selected_character_id,
            completion_kind=completion_kind,
            completion_payload=completion_payload,
        )
        if detail_level != "marginal":
            self._latest_analysis_load_request = presentation.load
        self._analysis_load_token += 1
        token = self._analysis_load_token
        self._pending_analysis_load = (token, presentation, history)
        if self._analysis_load_worker is None:
            self._start_pending_analysis_load()

    def _start_pending_analysis_load(self) -> None:
        pending = self._pending_analysis_load
        if pending is None or self._analysis_load_worker is not None:
            return
        self._pending_analysis_load = None
        token, presentation, history = pending
        worker = WorkerThread(
            target=lambda: BattleReportAnalysisLoadService.load(
                history,
                presentation.load,
            ),
            parent=self,
        )
        self._analysis_load_worker = worker
        worker.result_ready.connect(
            lambda result, current=token, request=presentation: (
                self._analysis_load_ready(current, request, result)
            )
        )
        worker.error.connect(
            lambda message, current=token, request=presentation: (
                self._analysis_load_failed(current, request, message)
            )
        )
        worker.finished.connect(
            lambda current=worker: self._analysis_load_finished(current)
        )
        worker.start()

    def _analysis_load_ready(
        self,
        token: int,
        request: _AnalysisPresentationRequest,
        result: object,
    ) -> None:
        if (
            token != self._analysis_load_token
            or not self._analysis_request_is_current(request)
        ):
            return
        if not isinstance(result, BattleReportAnalysisLoadResult):
            self._analysis_load_failed(token, request, "后台分析返回了未知结果")
            return
        analysis = result.analysis
        record_id = request.load.battle_record_id
        if analysis is None or not analysis.timeline_hits:
            self._page.end_analysis_details()
            self._page.clear_analysis(
                "当前记录只有聚合摘要，或所选时段没有正式逐击证据。"
            )
            self._build_snapshot_controller.refresh(record_id)
            return
        if result.target_catalog is not None:
            self._page.set_target_catalog(result.target_catalog)
        if result.target_catalog_error is not None:
            log_event(
                "WARNING",
                "battle_report.target_catalog_load_failed",
                "读取战报目标静态目录失败",
                self._history_operation_context(),
                phase="failed",
                battle_record_id=record_id,
                error=safe_exception(result.target_catalog_error),
            )
        self._page.end_analysis_details()
        if request.load.detail_level == "marginal":
            self._page.set_marginal_analysis(analysis)
        else:
            self._page.set_analysis(
                analysis,
                selected_character_id=request.selected_character_id,
            )
        if request.completion_kind:
            self._page.complete_analysis_details(
                request.completion_kind,
                request.completion_payload,
            )
        self._build_snapshot_controller.refresh(record_id)
        if request.load.start_us is not None and request.load.end_us is not None:
            self._save_analysis_range(
                record_id,
                request.load.start_us,
                request.load.end_us,
            )
        elif request.load.detail_scope is not None:
            self._save_analysis_range(
                record_id,
                analysis.range_start_us,
                analysis.range_end_us,
            )
        elif request.persist_full_range:
            self._save_analysis_range(record_id, None, None)

    def _analysis_load_failed(
        self,
        token: int,
        request: _AnalysisPresentationRequest,
        message: str,
    ) -> None:
        if (
            token != self._analysis_load_token
            or not self._analysis_request_is_current(request)
        ):
            return
        self._page.end_analysis_details()
        self._page.clear_analysis(f"读取战报逐击分析失败：{message}")
        log_event(
            "WARNING",
            "battle_report.analysis_load_failed",
            "读取战报长页分析失败",
            self._history_operation_context(),
            phase="failed",
            battle_record_id=request.load.battle_record_id,
            error=message,
        )

    def _analysis_load_finished(self, worker: WorkerThread) -> None:
        if worker is not self._analysis_load_worker:
            return
        self._analysis_load_worker = None
        worker.deleteLater()
        self._start_pending_analysis_load()

    def _load_analysis_details(self, kind: str, payload: object = None) -> None:
        record_id = self._latest_state.battle_record_id
        if record_id is None or self.is_running():
            return
        detail_level = "hit" if kind == "composition" else kind
        if detail_level not in {"hit", "buff"}:
            return
        base = self._latest_analysis_load_request
        if base is None or base.battle_record_id != record_id:
            base = BattleReportAnalysisLoadRequest(
                battle_record_id=record_id,
                detail_scope=self._page.detail_scope(),
            )
        self._load_analysis(
            record_id,
            start_us=base.start_us,
            end_us=base.end_us,
            selected_character_id=self._page.analysis_character_id(),
            detail_scope=base.detail_scope,
            detail_level=detail_level,
            completion_kind=kind,
            completion_payload=payload,
        )

    def _load_marginal_analysis(
        self,
        character_id: int,
        detail_scope: object = None,
        profiles: object = None,
    ) -> None:
        record_id = self._latest_state.battle_record_id
        if record_id is None or self.is_running():
            return
        scope = str(detail_scope) if detail_scope in {"first", "second"} else None
        if not isinstance(profiles, list):
            return
        try:
            candidate = BattleMarginalCandidateService.freeze(
                record_id,
                profiles,
                equipment_editable=self._page.marginal_equipment_editable(),
                disabled_inferred_fact_ids=(
                    self._page.marginal_disabled_inferred_fact_ids()
                ),
            )
        except (TypeError, ValueError):
            return
        self._load_analysis(
            record_id,
            selected_character_id=int(character_id),
            detail_scope=scope,
            detail_level="marginal",
            marginal_candidate=candidate,
            completion_kind="marginal",
        )

    def _analysis_request_is_current(
        self,
        request: _AnalysisPresentationRequest,
    ) -> bool:
        account = self._app_context.account
        return (
            request.account_id == account.active_account_id
            and request.generation == self._app_context.generation
            and not self._closing
        )

    def _save_analysis_range(
        self,
        battle_record_id: int,
        start_us: int | None,
        end_us: int | None,
    ) -> None:
        try:
            self._current_history_service().update_analysis_state(
                battle_record_id=battle_record_id,
                start_us=start_us,
                end_us=end_us,
                character_id=self._page.analysis_character_id(),
            )
        except StaleBattleReportContextError:
            return
        except Exception as error:
            log_event(
                "WARNING",
                "battle_report.analysis_state_save_failed",
                "保存战报分析时段失败",
                self._history_operation_context(),
                phase="failed",
                battle_record_id=battle_record_id,
                error=safe_exception(error),
            )

    def _save_analysis_character(self, character_id: int) -> None:
        record_id = self._latest_state.battle_record_id
        selected_range = self._page.analysis_range()
        if record_id is None or selected_range is None or self.is_running():
            return
        try:
            self._current_history_service().update_analysis_state(
                battle_record_id=record_id,
                start_us=selected_range[0],
                end_us=selected_range[1],
                character_id=character_id,
            )
        except StaleBattleReportContextError:
            return
        except Exception as error:
            log_event(
                "WARNING",
                "battle_report.analysis_character_save_failed",
                "保存战报分析角色失败",
                self._history_operation_context(),
                phase="failed",
                battle_record_id=record_id,
                error=safe_exception(error),
            )

    def _save_target_condition(self, condition: object) -> None:
        record_id = self._latest_state.battle_record_id
        if record_id is None or self.is_running() or not isinstance(condition, dict):
            return
        selected_range = self._page.analysis_range()
        selected_character_id = self._page.analysis_character_id()
        try:
            self._current_history_service().save_target_condition(
                record_id,
                condition,
            )
        except StaleBattleReportContextError:
            return
        except Exception as error:
            self._show_history_error("保存敌方条件失败", error)
            return
        self._load_analysis(
            record_id,
            start_us=(None if selected_range is None else selected_range[0]),
            end_us=(None if selected_range is None else selected_range[1]),
            selected_character_id=selected_character_id,
        )
