# 编排一次可丢弃的战报边际草稿，并隔离持久化角色副本控制器。
"""Session coordinator for explicit fixed-axis marginal recalculation."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass

from src.features.battle_report.page import BattleReportPage
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidateService,
)
from src.services.battle_report_history_service import BattleReportHistoryService


@dataclass(slots=True)
class _MarginalSession:
    record_id: int
    entry_editor_data: dict
    revision: int = 0


class BattleMarginalSessionController:
    """Own the entry baseline and invalidate stale asynchronous results."""

    def __init__(
        self,
        *,
        page: BattleReportPage,
        service_provider: Callable[[], BattleReportHistoryService],
        record_id_provider: Callable[[], int | None],
        is_running: Callable[[], bool],
        reload_analysis: Callable[..., None],
        invalidate_analysis: Callable[[], None],
        show_error: Callable[[str, Exception], None],
    ) -> None:
        self._page = page
        self._service_provider = service_provider
        self._record_id_provider = record_id_provider
        self._is_running = is_running
        self._reload_analysis = reload_analysis
        self._invalidate_analysis = invalidate_analysis
        self._show_error = show_error
        self._session: _MarginalSession | None = None
        page.marginal_requested.connect(self.open)
        page.marginal_recalculate_requested.connect(self.recalculate)
        page.marginal_reset_requested.connect(self.reset)
        page.marginal_draft_changed.connect(self.draft_changed)
        page.marginal_closed.connect(self.close)

    def open(self) -> None:
        record_id = self._editable_record_id()
        if record_id is None:
            return
        try:
            editor_data = self._service_provider().load_build_editor_data(record_id)
        except Exception as error:
            self._show_error("无法打开固定轴边际计算", error)
            return
        equipment_editable = bool(
            editor_data.get(
                "equipment_editable",
                editor_data.get("marginal_equipment_editable", True),
            )
        )
        candidate_data = BattleMarginalCandidateService.prepare_editor_data(
            editor_data,
            equipment_editable=equipment_editable,
        )
        self._session = _MarginalSession(
            record_id=record_id,
            entry_editor_data=deepcopy(candidate_data),
        )
        self._page.show_marginal(candidate_data)

    def reset(self) -> None:
        session = self._current_session()
        if session is None:
            return
        session.revision += 1
        self._invalidate_analysis()
        self._page.reset_marginal_draft(deepcopy(session.entry_editor_data))

    def draft_changed(self) -> None:
        session = self._current_session()
        if session is None:
            return
        session.revision += 1
        self._invalidate_analysis()
        self._page.invalidate_marginal_result()

    def recalculate(self, profiles: object) -> None:
        session = self._current_session()
        if session is None:
            return
        if not isinstance(profiles, list):
            self._show_error("无法重算边际", ValueError("角色候选配置格式无效"))
            return
        try:
            candidate = BattleMarginalCandidateService.freeze(
                session.record_id,
                profiles,
                equipment_editable=self._page.marginal_equipment_editable(),
                disabled_inferred_fact_ids=(
                    self._page.marginal_disabled_inferred_fact_ids()
                ),
            )
        except (TypeError, ValueError) as error:
            self._show_error("无法重算边际", error)
            return
        self._reload_analysis(
            session.record_id,
            selected_character_id=self._page.analysis_character_id(),
            detail_scope=self._page.marginal_detail_scope(),
            detail_level="marginal",
            marginal_candidate=candidate,
            completion_kind="marginal",
        )

    def close(self) -> None:
        if self._session is not None:
            self._invalidate_analysis()
        self._session = None

    def _current_session(self) -> _MarginalSession | None:
        session = self._session
        record_id = self._editable_record_id()
        if session is None or record_id != session.record_id:
            return None
        return session

    def _editable_record_id(self) -> int | None:
        record_id = self._record_id_provider()
        if record_id is None or self._is_running():
            return None
        return int(record_id)
