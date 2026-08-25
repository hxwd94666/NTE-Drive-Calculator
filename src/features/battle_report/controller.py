# 编排战报采集、页面投影和悬浮窗生命周期。
"""Controller for battle capture, page projection and overlay lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from src.app.context import AppContext
from src.domain.battle_report import (
    BattleCaptureState,
    BattleRetentionMutation,
    EMPTY_BATTLE_CAPTURE_STATE,
    StoredBattleSummary,
)
from src.features.battle_report.history_dialog import BattleReportHistoryDialog
from src.features.battle_report.transfer_dialog import BattleReportTransferDialog
from src.features.battle_report.analysis_controller_mixin import (
    BattleReportAnalysisControllerMixin,
)
from src.features.battle_report.build_snapshot_controller import (
    BattleBuildSnapshotController,
)
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
from src.services.battle_report_transfer_service import BattleReportTransferService


BattlePersistenceFactory = Callable[
    [BattleReportPersistenceDependencies, OperationContext],
    BattleSummaryWriter,
]
BattleHistoryFactory = Callable[
    [BattleReportPersistenceDependencies],
    BattleReportHistoryService,
]
BattleTransferFactory = Callable[[], BattleReportTransferService]


class BattleReportController(BattleReportAnalysisControllerMixin, QObject):
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
        transfer_factory: BattleTransferFactory,
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
        self._transfer_factory = transfer_factory
        asset_root = app_context.paths.asset_dir / "game_ui"
        self._asset_root = asset_root
        self._page = BattleReportPage(game_ui_asset_root=asset_root)
        self._overlay = BattleReportOverlay(game_ui_asset_root=asset_root)
        self._service: BattleCaptureService | None = None
        self._history_service: BattleReportHistoryService | None = None
        self._history_dialog: BattleReportHistoryDialog | None = None
        self._transfer_dialog: BattleReportTransferDialog | None = None
        self._transfer_service: BattleReportTransferService | None = None
        self._history_restored_generation: int | None = None
        self._operation_token = 0
        self._frozen_account_id: str | None = None
        self._frozen_generation: int | None = None
        self._resume_inventory = False
        self._closing = False
        self._overlay_capture_active = False
        self._latest_state = EMPTY_BATTLE_CAPTURE_STATE
        self._initialize_analysis_loading()
        self._page.start_requested.connect(self.start)
        self._page.stop_requested.connect(self.stop)
        self._page.overlay_visibility_changed.connect(self._set_overlay_visible)
        self._page.overlay_passthrough_changed.connect(self._overlay.set_passthrough)
        self._page.detail_scope_changed.connect(self._change_detail_scope)
        self._page.save_result_requested.connect(self._save_current_result)
        self._page.history_requested.connect(self._show_history)
        self._page.export_requested.connect(self._show_transfer_dialog)
        self._page.analysis_range_requested.connect(self._load_analysis_range)
        self._page.analysis_range_reset_requested.connect(
            self._reset_analysis_range
        )
        self._page.analysis_character_changed.connect(
            self._save_analysis_character
        )
        self._page.target_condition_save_requested.connect(
            self._save_target_condition
        )
        self._build_snapshot_controller = BattleBuildSnapshotController(
            page=self._page,
            dialog_parent=self._dialog_parent,
            service_provider=self._current_history_service,
            record_id_provider=lambda: self._latest_state.battle_record_id,
            is_running=self.is_running,
            reload_analysis=self._load_analysis,
            show_error=self._show_history_error,
        )
        self._page.analysis_details_requested.connect(
            self._load_analysis_details
        )
        self._page.marginal_analysis_requested.connect(
            self._load_marginal_analysis
        )
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
        self._invalidate_analysis_loading()
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
                "无法开始战报",
                f"读取抓包设置失败，未启动战报采集：{error}",
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
                    "无法开始战报",
                    f"停止背包同步失败，未启动战报采集：{error}",
                )
                return
        self._overlay_capture_active = True
        self._overlay.clear_summary()
        show_report = getattr(self._page, "show_report", None)
        if callable(show_report):
            show_report()
        self._page.clear_analysis("采集中；结束并保存正式逐击后生成长页分析。")
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
            static_database_path=self._app_context.paths.static_database_path,
        )
        self._history_service = self._history_factory(persistence_dependencies)
        self._history_restored_generation = self._app_context.generation
        raw_capture_enabled = bool(sync_settings.get("raw_capture_enabled"))
        raw_capture_directory = account.log_dir / "nte_core" / "raw_capture"
        service = BattleCaptureService(
            client_factory=lambda: self._client_factory(raw_capture_directory),
            operation_context=operation,
            device_name=configured_device or None,
            summary_writer=self._persistence_factory(
                persistence_dependencies,
                operation,
            ),
            raw_capture_enabled=raw_capture_enabled,
            raw_capture_directory=raw_capture_directory,
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
        self._invalidate_analysis_loading()
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
        transfer_dialog = self._transfer_dialog
        if transfer_dialog is not None:
            transfer_dialog.reject()
        self._transfer_dialog = None
        self._transfer_service = None
        self._overlay.close()

    def reset_account_state(self) -> None:
        if self.is_running():
            raise RuntimeError("战报采集期间不能切换账号")
        self._operation_token += 1
        self._invalidate_analysis_loading()
        self._service = None
        history_dialog = self._history_dialog
        if history_dialog is not None:
            history_dialog.reject()
        self._history_dialog = None
        transfer_dialog = self._transfer_dialog
        if transfer_dialog is not None:
            transfer_dialog.reject()
        self._transfer_dialog = None
        self._transfer_service = None
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
                self._load_analysis(
                    state.battle_record_id,
                    detail_scope=self._page.detail_scope(),
                )
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
            static_database_path=self._app_context.paths.static_database_path,
        )
        history_service = self._history_factory(dependencies)
        try:
            stored = history_service.restore_last_summary()
        except Exception as error:
            log_event(
                "WARNING",
                "battle_report.history_restore_failed",
                "恢复上次战报失败",
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
            message="已恢复上次保存的战报。",
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
                "保存战报页面状态失败",
                OperationContext.create(
                    "battle_report",
                    account_id=self._app_context.account.active_account_id,
                    context_generation=self._app_context.generation,
                ),
                phase="failed",
                battle_record_id=record_id,
                error=safe_exception(error),
            )

    def _change_detail_scope(self, detail_scope: str) -> None:
        self._save_detail_scope(detail_scope)
        record_id = self._latest_state.battle_record_id
        if record_id is None or self.is_running():
            return
        self._load_analysis(record_id, detail_scope=detail_scope)

    def _save_current_result(self) -> None:
        record_id = self._latest_state.battle_record_id
        if record_id is None or self.is_running():
            return
        try:
            mutation = self._current_history_service().save_record(record_id)
        except Exception as error:
            self._show_history_error("保存伤害结果失败", error)
            return
        self._apply_retention_mutation(mutation, message="战报已手动保存。")

    def _show_history(self) -> None:
        if self.is_running():
            return
        try:
            entries = self._current_history_service().list_entries()
        except Exception as error:
            self._show_history_error("读取历史战报失败", error)
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

    def _show_transfer_dialog(self) -> None:
        if self.is_running():
            QMessageBox.information(
                self._dialog_parent,
                "战报采集中",
                "请先结束当前采集，再导出或读取战报包。",
            )
            return
        try:
            service = self._transfer_factory()
            entries = service.list_entries()
            account_name = service.current_account_name()
        except Exception as error:
            self._show_history_error("打开战报包页面失败", error)
            return
        dialog = BattleReportTransferDialog(parent=self._dialog_parent)
        dialog.set_account_name(account_name)
        dialog.set_entries(entries)
        dialog.account_name_save_requested.connect(self._save_transfer_account_name)
        dialog.export_requested.connect(self._export_selected_reports)
        dialog.import_requested.connect(self._import_report_bundle)
        self._transfer_service = service
        self._transfer_dialog = dialog
        dialog.exec()
        if self._transfer_dialog is dialog:
            self._transfer_dialog = None
            self._transfer_service = None

    def _save_transfer_account_name(self, value: str) -> None:
        dialog = self._transfer_dialog
        service = self._transfer_service
        if dialog is None or service is None:
            return
        dialog.clear_error()
        try:
            saved = service.rename_current_account(value)
        except Exception as error:
            dialog.show_error(f"保存昵称失败：{error}")
            return
        dialog.set_account_name(saved)
        QMessageBox.information(dialog, "账号昵称", "账号昵称已保存。")

    def _export_selected_reports(self, report_ids: object) -> None:
        dialog = self._transfer_dialog
        service = self._transfer_service
        if dialog is None or service is None:
            return
        if dialog.has_unsaved_account_name():
            dialog.show_error("账号昵称有未保存修改，请先点击“保存昵称”。")
            return
        selected = tuple(report_ids) if isinstance(report_ids, (tuple, list)) else ()
        if not selected:
            dialog.show_error("请至少选择一场战报。")
            return
        dialog.clear_error()
        try:
            suggested = service.suggested_filename()
        except Exception as error:
            dialog.show_error(f"准备导出失败：{error}")
            return
        target, _selected_filter = QFileDialog.getSaveFileName(
            dialog,
            "导出战报包",
            suggested,
            "NTE 战报包 (*.ntebr)",
        )
        if not target:
            return
        if not target.casefold().endswith(".ntebr"):
            target += ".ntebr"
        dialog.set_busy(True)
        try:
            outcome = service.export_reports(selected, target)
        except Exception as error:
            dialog.show_error(f"导出失败：{error}")
        else:
            QMessageBox.information(
                dialog,
                "导出完成",
                f"已导出 {outcome.report_count} 场战报。\n保存位置：{target}",
            )
        finally:
            dialog.set_busy(False)

    def _import_report_bundle(self) -> None:
        dialog = self._transfer_dialog
        service = self._transfer_service
        if dialog is None or service is None:
            return
        source, _selected_filter = QFileDialog.getOpenFileName(
            dialog,
            "读取战报包",
            "",
            "NTE 战报包 (*.ntebr)",
        )
        if not source:
            return
        dialog.clear_error()
        dialog.set_busy(True)
        try:
            outcome = service.import_bundle(source)
            dialog.set_entries(service.list_entries())
        except Exception as error:
            dialog.show_error(f"读取战报包失败：{error}")
        else:
            QMessageBox.information(
                dialog,
                "读取完成",
                (
                    f"已导入 {len(outcome.imported_record_ids)} 场战报；"
                    f"跳过 {outcome.skipped_existing_count} 场已有战报。"
                ),
            )
            self._refresh_history_dialog()
        finally:
            dialog.set_busy(False)

    def _view_history_record(self, battle_record_id: int) -> None:
        try:
            history_service = self._current_history_service()
            stored = history_service.load_summary(battle_record_id)
            if stored is None:
                raise RuntimeError("所选战报已经不存在")
            history_service.update_page_state(
                battle_record_id=battle_record_id,
                detail_scope="current",
            )
            stored = replace(
                stored,
                detail_scope="current",
                analysis_start_us=None,
                analysis_end_us=None,
            )
        except Exception as error:
            self._show_history_error("读取战报详情失败", error)
            self._refresh_history_dialog()
            return
        state = BattleCaptureState(
            phase="history",
            message="正在查看历史战报。",
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
                message = "战报已取消手动保存。"
            else:
                mutation = history_service.save_record(battle_record_id)
                message = "战报已手动保存。"
        except Exception as error:
            self._show_history_error("更新战报保存状态失败", error)
            return
        self._apply_retention_mutation(mutation, message=message)

    def _delete_history_record(self, battle_record_id: int) -> None:
        answer = QMessageBox.question(
            self._history_dialog or self._dialog_parent,
            "删除战报",
            "确定删除这条战报吗？删除后不能恢复。",
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
            self._show_history_error("删除战报失败", error)
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
            self._show_history_error("刷新历史战报失败", error)
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
            static_database_path=self._app_context.paths.static_database_path,
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
        show_report = getattr(self._page, "show_report", None)
        if callable(show_report):
            show_report()
        self._latest_state = state
        self._page.update_state(state)
        self._page.set_detail_scope(stored.detail_scope)
        if (
            stored.analysis_start_us is not None
            and stored.analysis_end_us is not None
        ):
            self._load_analysis(
                stored.battle_record_id,
                start_us=stored.analysis_start_us,
                end_us=stored.analysis_end_us,
                selected_character_id=stored.analysis_character_id,
            )
        else:
            self._load_analysis(
                stored.battle_record_id,
                selected_character_id=stored.analysis_character_id,
                detail_scope=stored.detail_scope,
            )
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
