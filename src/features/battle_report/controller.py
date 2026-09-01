# 编排战报采集、页面投影和悬浮窗生命周期。
"""Controller for battle capture, page projection and overlay lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from src.i18n import tr
from src.app.context import AppContext
from src.domain.battle_report import (
    BattleCaptureState,
    BattleRetentionMutation,
    EMPTY_BATTLE_CAPTURE_STATE,
    StoredBattleSummary,
)
from src.features.battle_report.history_dialog import BattleReportHistoryDialog
from src.features.battle_report.overlay import BattleReportOverlay
from src.features.battle_report.page import BattleReportPage
from src.observability import OperationContext
from src.observability.operation import log_event
from src.observability.redaction import safe_exception
from src.services.battle_capture_service import (
    BattleCaptureService,
    BattleCoreClient,
    BattleSummaryWriter,
)
from src.services.battle_report_persistence_service import (
    BattleReportPersistenceDependencies,
)
from src.services.battle_report_history_service import (
    BattleReportHistoryService,
    StaleBattleReportContextError,
)


BattlePersistenceFactory = Callable[
    [BattleReportPersistenceDependencies, OperationContext],
    BattleSummaryWriter,
]
BattleHistoryFactory = Callable[
    [BattleReportPersistenceDependencies],
    BattleReportHistoryService,
]


class BattleReportController(QObject):
    """Own the feature worker and bridge its callbacks onto the Qt thread."""

    _state_received = Signal(int, object)

    def __init__(
        self,
        *,
        app_context: AppContext,
        dialog_parent: QWidget,
        inventory_sync_is_running: Callable[[], bool],
        stop_inventory_sync: Callable[[], None],
        start_inventory_sync: Callable[[], None],
        client_factory: Callable[[Path], BattleCoreClient],
        persistence_factory: BattlePersistenceFactory,
        history_factory: BattleHistoryFactory,
    ) -> None:
        super().__init__(dialog_parent)
        self._app_context = app_context
        self._dialog_parent = dialog_parent
        self._inventory_sync_is_running = inventory_sync_is_running
        self._stop_inventory_sync = stop_inventory_sync
        self._start_inventory_sync = start_inventory_sync
        self._client_factory = client_factory
        self._persistence_factory = persistence_factory
        self._history_factory = history_factory
        asset_root = app_context.paths.asset_dir / "game_ui"
        self._asset_root = asset_root
        self._page = BattleReportPage(game_ui_asset_root=asset_root)
        self._overlay = BattleReportOverlay(game_ui_asset_root=asset_root)
        self._service: BattleCaptureService | None = None
        self._history_service: BattleReportHistoryService | None = None
        self._history_dialog: BattleReportHistoryDialog | None = None
        self._history_restored_generation: int | None = None
        self._operation_token = 0
        self._frozen_account_id: str | None = None
        self._frozen_generation: int | None = None
        self._resume_inventory = False
        self._closing = False
        self._overlay_capture_active = False
        self._latest_state = EMPTY_BATTLE_CAPTURE_STATE
        self._page.start_requested.connect(self.start)
        self._page.stop_requested.connect(self.stop)
        self._page.overlay_visibility_changed.connect(self._set_overlay_visible)
        self._page.overlay_passthrough_changed.connect(self._overlay.set_passthrough)
        self._page.detail_scope_changed.connect(self._save_detail_scope)
        self._page.save_result_requested.connect(self._save_current_result)
        self._page.history_requested.connect(self._show_history)
        self._state_received.connect(self._apply_state)
        self._restore_last_history()

    def build_page(self) -> QWidget:
        self._restore_last_history()
        return self._page

    def is_running(self) -> bool:
        service = self._service
        return bool(service is not None and service.is_running)

    def start(self) -> None:
        if self.is_running():
            return
        self._operation_token += 1
        token = self._operation_token
        account = self._app_context.account
        self._frozen_account_id = account.active_account_id
        self._frozen_generation = self._app_context.generation
        try:
            sync_settings = self._app_context.account_settings.load("sync")
        except Exception as error:
            QMessageBox.warning(
                self._dialog_parent,
                tr("无法开始战报"),
                tr("读取抓包设置失败，未启动战报采集：{error}", error=error),
            )
            return
        configured_device = str(sync_settings.get("capture_device_id") or "").strip()
        self._resume_inventory = self._inventory_sync_is_running()
        if self._resume_inventory:
            try:
                self._stop_inventory_sync()
            except Exception as error:
                self._resume_inventory = False
                QMessageBox.warning(
                    self._dialog_parent,
                    tr("无法开始战报"),
                    tr("停止背包同步失败，未启动战报采集：{error}", error=error),
                )
                return
        self._overlay_capture_active = True
        self._overlay.clear_summary()
        if self._page.overlay_toggle.isChecked():
            self._overlay.show_overlay()
        operation = OperationContext.create(
            "battle_report",
            account_id=account.active_account_id,
            context_generation=self._app_context.generation,
        )
        persistence_dependencies = BattleReportPersistenceDependencies(
            account_id=account.active_account_id,
            user_database_path=account.user_database_path,
            generation=self._app_context.generation,
        )
        self._history_service = self._history_factory(persistence_dependencies)
        self._history_restored_generation = self._app_context.generation
        battle_data_dir = account.log_dir / "nte_core" / "battle_report"
        service = BattleCaptureService(
            client_factory=lambda: self._client_factory(battle_data_dir),
            operation_context=operation,
            device_name=configured_device or None,
            summary_writer=self._persistence_factory(
                persistence_dependencies,
                operation,
            ),
        )
        service.add_state_handler(
            lambda state, operation_token=token: self._state_received.emit(
                operation_token, state
            )
        )
        self._service = service
        service.start()

    def stop(self) -> None:
        self._overlay_capture_active = False
        self._overlay.hide()
        service = self._service
        if service is not None:
            service.request_stop()

    def close(self) -> None:
        self._closing = True
        self._operation_token += 1
        service = self._service
        if service is not None:
            service.close()
        self._service = None
        self._resume_inventory = False
        self._overlay_capture_active = False
        history_dialog = self._history_dialog
        if history_dialog is not None:
            history_dialog.reject()
        self._history_dialog = None
        self._overlay.close()

    def reset_account_state(self) -> None:
        if self.is_running():
            raise RuntimeError(tr("战报采集期间不能切换账号"))
        self._operation_token += 1
        self._service = None
        history_dialog = self._history_dialog
        if history_dialog is not None:
            history_dialog.reject()
        self._history_dialog = None
        self._history_service = None
        self._history_restored_generation = None
        self._latest_state = EMPTY_BATTLE_CAPTURE_STATE
        self._page.clear_summary()
        self._page.update_state(EMPTY_BATTLE_CAPTURE_STATE)
        self._overlay.clear_summary()
        self._overlay.hide()
        self._overlay_capture_active = False
        self._frozen_account_id = None
        self._frozen_generation = None
        self._resume_inventory = False
        self._restore_last_history()

    def _apply_state(self, token: int, state: object) -> None:
        if token != self._operation_token or not isinstance(state, BattleCaptureState):
            return
        if (
            self._frozen_account_id != self._app_context.account.active_account_id
            or self._frozen_generation != self._app_context.generation
        ):
            return
        self._latest_state = state
        self._page.update_state(state)
        if state.summary is not None:
            self._overlay.update_summary(state.summary)
        if (
            state.running
            and self._overlay_capture_active
            and self._page.overlay_toggle.isChecked()
        ):
            self._overlay.show_overlay()
        if state.phase in {"stopped", "error"}:
            self._overlay_capture_active = False
            self._overlay.hide()
            self._service = None
            if state.battle_record_id is not None:
                self._save_detail_scope(self._page.detail_scope())
            self._restore_inventory_sync()

    def _restore_inventory_sync(self) -> None:
        should_resume = self._resume_inventory
        self._resume_inventory = False
        if not should_resume or self._closing:
            return
        if (
            self._frozen_account_id == self._app_context.account.active_account_id
            and self._frozen_generation == self._app_context.generation
        ):
            self._start_inventory_sync()

    def _set_overlay_visible(self, visible: bool) -> None:
        if visible and self._overlay_capture_active and self.is_running():
            self._overlay.show_overlay()
        else:
            self._overlay.hide()

    def _restore_last_history(self) -> None:
        if self.is_running():
            return
        generation = self._app_context.generation
        if self._history_restored_generation == generation:
            return
        account = self._app_context.account
        dependencies = BattleReportPersistenceDependencies(
            account_id=account.active_account_id,
            user_database_path=account.user_database_path,
            generation=generation,
        )
        history_service = self._history_factory(dependencies)
        try:
            stored = history_service.restore_last_summary()
        except Exception as error:
            log_event(
                "WARNING",
                "battle_report.history_restore_failed",
                tr("恢复上次战报失败"),
                OperationContext.create(
                    "battle_report",
                    account_id=account.active_account_id,
                    context_generation=generation,
                ),
                phase="failed",
                error=safe_exception(error),
            )
            return
        self._history_service = history_service
        self._history_restored_generation = generation
        if stored is None:
            self._latest_state = EMPTY_BATTLE_CAPTURE_STATE
            self._page.clear_summary()
            self._page.update_state(EMPTY_BATTLE_CAPTURE_STATE)
            return
        state = BattleCaptureState(
            phase="history",
            message=tr("已恢复上次保存的战报。"),
            running=False,
            summary=stored.summary,
            persistence_status="loaded_history",
            battle_record_id=stored.battle_record_id,
            retention_kind=stored.retention_kind,
        )
        self._apply_stored_summary(state, stored)

    def _save_detail_scope(self, detail_scope: str) -> None:
        record_id = self._latest_state.battle_record_id
        history_service = self._history_service
        if record_id is None or history_service is None:
            return
        try:
            history_service.update_page_state(
                battle_record_id=record_id,
                detail_scope=detail_scope,
            )
        except StaleBattleReportContextError:
            return
        except Exception as error:
            log_event(
                "WARNING",
                "battle_report.page_state_save_failed",
                tr("保存战报页面状态失败"),
                OperationContext.create(
                    "battle_report",
                    account_id=self._app_context.account.active_account_id,
                    context_generation=self._app_context.generation,
                ),
                phase="failed",
                battle_record_id=record_id,
                error=safe_exception(error),
            )

    def _save_current_result(self) -> None:
        record_id = self._latest_state.battle_record_id
        if record_id is None or self.is_running():
            return
        try:
            mutation = self._current_history_service().save_record(record_id)
        except Exception as error:
            self._show_history_error(tr("保存伤害结果失败"), error)
            return
        self._apply_retention_mutation(mutation, message=tr("战报已手动保存。"))

    def _show_history(self) -> None:
        if self.is_running():
            return
        try:
            entries = self._current_history_service().list_entries()
        except Exception as error:
            self._show_history_error(tr("读取历史战报失败"), error)
            return
        dialog = BattleReportHistoryDialog(
            game_ui_asset_root=self._asset_root,
            parent=self._dialog_parent,
        )
        dialog.view_requested.connect(self._view_history_record)
        dialog.retention_toggle_requested.connect(self._toggle_history_retention)
        dialog.delete_requested.connect(self._delete_history_record)
        dialog.set_entries(entries)
        self._history_dialog = dialog
        dialog.exec()
        if self._history_dialog is dialog:
            self._history_dialog = None

    def _view_history_record(self, battle_record_id: int) -> None:
        try:
            history_service = self._current_history_service()
            stored = history_service.load_summary(battle_record_id)
            if stored is None:
                raise RuntimeError(tr("所选战报已经不存在"))
            history_service.update_page_state(
                battle_record_id=battle_record_id,
                detail_scope="current",
            )
        except Exception as error:
            self._show_history_error(tr("读取战报详情失败"), error)
            self._refresh_history_dialog()
            return
        state = BattleCaptureState(
            phase="history",
            message=tr("正在查看历史战报。"),
            running=False,
            summary=stored.summary,
            persistence_status="loaded_history",
            battle_record_id=stored.battle_record_id,
            retention_kind=stored.retention_kind,
        )
        self._apply_stored_summary(state, stored)
        dialog = self._history_dialog
        if dialog is not None:
            dialog.accept()

    def _toggle_history_retention(
        self,
        battle_record_id: int,
        current_kind: str,
    ) -> None:
        try:
            history_service = self._current_history_service()
            if current_kind == "manual":
                mutation = history_service.unmark_record(battle_record_id)
                message = tr("战报已取消手动保存。")
            else:
                mutation = history_service.save_record(battle_record_id)
                message = tr("战报已手动保存。")
        except Exception as error:
            self._show_history_error(tr("更新战报保存状态失败"), error)
            return
        self._apply_retention_mutation(mutation, message=message)

    def _delete_history_record(self, battle_record_id: int) -> None:
        answer = QMessageBox.question(
            self._history_dialog or self._dialog_parent,
            tr("删除战报"),
            tr("确定删除这条战报吗？删除后不能恢复。"),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            deleted = self._current_history_service().delete_record(
                battle_record_id
            )
        except Exception as error:
            self._show_history_error(tr("删除战报失败"), error)
            return
        if not deleted:
            self._refresh_history_dialog()
            return
        log_event(
            "INFO",
            "battle_report.record_deleted",
            "用户删除战报记录",
            self._history_operation_context(),
            phase="deleted",
            battle_record_id=battle_record_id,
        )
        if self._latest_state.battle_record_id == battle_record_id:
            self._reload_after_history_removal()
        self._refresh_history_dialog()

    def _apply_retention_mutation(
        self,
        mutation: BattleRetentionMutation,
        *,
        message: str,
    ) -> None:
        current_id = self._latest_state.battle_record_id
        if current_id == mutation.battle_record_id:
            self._latest_state = replace(
                self._latest_state,
                message=message,
                retention_kind=mutation.retention_kind,
            )
            self._page.update_state(self._latest_state)
        elif current_id in mutation.pruned_battle_record_ids:
            self._reload_after_history_removal()
        log_event(
            "INFO",
            "battle_report.retention_changed",
            "更新战报保留状态",
            self._history_operation_context(),
            phase="updated",
            battle_record_id=mutation.battle_record_id,
            retention_kind=mutation.retention_kind,
            changed=mutation.changed,
            pruned_record_count=len(mutation.pruned_battle_record_ids),
        )
        self._refresh_history_dialog()

    def _reload_after_history_removal(self) -> None:
        self._history_restored_generation = None
        self._latest_state = EMPTY_BATTLE_CAPTURE_STATE
        self._page.clear_summary()
        self._page.update_state(EMPTY_BATTLE_CAPTURE_STATE)
        self._overlay.clear_summary()
        self._overlay.hide()
        self._overlay_capture_active = False
        self._restore_last_history()

    def _refresh_history_dialog(self) -> None:
        dialog = self._history_dialog
        if dialog is None:
            return
        try:
            entries = self._current_history_service().list_entries()
        except Exception as error:
            self._show_history_error(tr("刷新历史战报失败"), error)
            return
        dialog.set_entries(entries)

    def _current_history_service(self) -> BattleReportHistoryService:
        generation = self._app_context.generation
        history_service = self._history_service
        if history_service is not None and self._history_restored_generation == generation:
            return history_service
        account = self._app_context.account
        dependencies = BattleReportPersistenceDependencies(
            account_id=account.active_account_id,
            user_database_path=account.user_database_path,
            generation=generation,
        )
        history_service = self._history_factory(dependencies)
        self._history_service = history_service
        self._history_restored_generation = generation
        return history_service

    def _apply_stored_summary(
        self,
        state: BattleCaptureState,
        stored: StoredBattleSummary,
    ) -> None:
        self._latest_state = state
        self._page.update_state(state)
        self._page.set_detail_scope(stored.detail_scope)
        self._overlay.update_summary(stored.summary)
        self._overlay_capture_active = False
        self._overlay.hide()

    def _show_history_error(self, title: str, error: Exception) -> None:
        QMessageBox.warning(
            self._history_dialog or self._dialog_parent,
            title,
            str(error),
        )
        log_event(
            "WARNING",
            "battle_report.history_action_failed",
            title,
            self._history_operation_context(),
            phase="failed",
            error=safe_exception(error),
        )

    def _history_operation_context(self) -> OperationContext:
        account = self._app_context.account
        return OperationContext.create(
            "battle_report",
            account_id=account.active_account_id,
            context_generation=self._app_context.generation,
        )
