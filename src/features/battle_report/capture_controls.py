# 管理战报重录确认、暂存丢弃和采集会话专属全局热键。
"""Battle capture restart controls shared by the report controller."""

from __future__ import annotations

from time import monotonic
from concurrent.futures import CancelledError

from PySide6.QtWidgets import QMessageBox

from src.domain.battle_report import BattleCaptureState
from src.integrations.global_hotkeys import GlobalHotkeyManager
from src.observability import OperationContext
from src.services.battle_capture_service import BattleCaptureService
from src.services.battle_report_persistence_service import BattleReportPersistenceDependencies
from src.features.battle_report.comparison_capture import ComparisonCapture
from src.features.battle_report.entry_guidance import capture_entry_allowed, capture_unavailable
from src.integrations.nte_core_protocol import NteCoreError
from src.integrations.nte_analysis_core import NativeAnalysisError


class BattleCaptureControlsMixin:
    """Own the restart intent without moving capture persistence into the UI."""

    _BATTLE_HOTKEY_OWNER = "battle_report"
    _RERECORD_CONFIRM_SECONDS = 1.5

    def _initialize_capture_controls(
        self,
        hotkey_manager: GlobalHotkeyManager,
    ) -> None:
        self._hotkey_manager = hotkey_manager
        self._restart_pending = False
        self._restart_resume_inventory = False
        self._rerecord_hotkey_armed_until = 0.0
        self._manual_stop_requested = False

    def start(self, *, preserve_inventory_pause: bool = False,
              continue_after_scene: bool = False) -> None:
        if self.is_running():
            return
        if not capture_entry_allowed(self, automatic=continue_after_scene):
            return
        policy = self._work_mode_service
        source = "native" if policy.allowed("native_battle") else "packet"
        capability = "native_battle" if source == "native" else "packet_capture"
        try:
            policy.require(capability)
        except PermissionError:
            capture_entry_allowed(self, automatic=continue_after_scene)
            return
        self._manual_stop_requested = False
        self._capture_guidance_enabled = not continue_after_scene
        self._capture_guidance_capability = capability
        self._capture_guidance_context = (
            self._app_context.account.active_account_id, self._app_context.generation,
        )
        self._capture_unavailable_notified = False
        compare = policy.allowed("compare_sources")
        if compare and self._comparison_client_factory is None:
            capture_unavailable(self, "未配置抓包对照入口，请检查组件与连接。")
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
            if preserve_inventory_pause:
                self._restore_inventory_sync()
            return
        configured_device = str(sync_settings.get("capture_device_id") or "").strip()
        if preserve_inventory_pause:
            self._resume_inventory = source == "native" and self._restart_resume_inventory
            self._restart_resume_inventory = False
        else:
            self._restart_pending = False
            self._restart_resume_inventory = False
            # Packet recording owns another capture handle; retain the inventory
            # decoder and its login baseline for the whole game session.
            # Only native recording takes the shared DLL session's exclusive lease.
            self._resume_inventory = source == "native" and self._inventory_sync_is_running()
        if self._resume_inventory and not preserve_inventory_pause:
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
        self._page.clear_summary()
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
        try:
            self._history_service = self._history_factory(persistence_dependencies)
        except (NteCoreError, NativeAnalysisError, OSError) as error:
            self._overlay_capture_active = False
            self._overlay.hide()
            capture_unavailable(self, str(error))
            self._restore_inventory_sync()
            return
        self._history_restored_generation = self._app_context.generation
        raw_capture_enabled = bool(sync_settings.get("raw_capture_enabled")) and policy.allowed("diagnostics")
        raw_capture_directory = account.log_dir / "nte_core" / "raw_capture"

        def check_client_start():
            if (self._manual_stop_requested or self._closing or self._restart_pending
                    or token != self._operation_token
                    or operation.account_id != self._app_context.account.active_account_id
                    or operation.context_generation != self._app_context.generation):
                raise CancelledError("本次战报启动已取消。")

        def create_client():
            check_client_start()
            return self._client_factory(raw_capture_directory, check_client_start)

        service = BattleCaptureService(
            client_factory=create_client,
            operation_guard=policy.require,
            operation_context=operation,
            device_name=configured_device or None,
            summary_writer=self._persistence_factory(
                persistence_dependencies,
                operation,
            ),
            raw_capture_enabled=raw_capture_enabled and not compare,
            raw_capture_directory=raw_capture_directory,
            required_source=source,
            comparison_id=operation.operation_id if compare else None,
        )
        if compare:
            packet_operation = OperationContext.create(
                "battle_report", account_id=account.active_account_id,
                context_generation=self._app_context.generation,
            )
            packet_factory = self._comparison_client_factory
            packet = BattleCaptureService(
                client_factory=lambda: packet_factory(raw_capture_directory),
                operation_guard=policy.require,
                operation_context=packet_operation, device_name=configured_device or None,
                summary_writer=self._persistence_factory(persistence_dependencies, packet_operation),
                raw_capture_enabled=raw_capture_enabled, raw_capture_directory=raw_capture_directory,
                required_source="packet",
                comparison_id=operation.operation_id,
            )
            service = ComparisonCapture(services={"native": service, "packet": packet}, parent=self,
                                        defer_packet_start=True)
            service.comparison_changed.connect(
                lambda snapshot, operation_token=token: self._apply_comparison(operation_token, snapshot)
            )
        service.add_state_handler(
            lambda state, operation_token=token: self._state_received.emit(
                operation_token, state
            )
        )
        self._service = service
        try:
            service.start()
        except (NteCoreError, OSError) as error:
            self._service = None
            self._overlay_capture_active = False
            self._overlay.hide()
            if isinstance(error, PermissionError) and not policy.allowed(capability):
                capture_entry_allowed(self, automatic=continue_after_scene)
            else:
                capture_unavailable(self, str(error))
            self._restore_inventory_sync()
            return
        self._start_battle_hotkeys()

    def _apply_comparison(self, token: int, snapshot: object) -> None:
        if (token == self._operation_token
                and self._frozen_account_id == self._app_context.account.active_account_id
                and self._frozen_generation == self._app_context.generation):
            self._page.set_capture_comparison(snapshot)

    def _start_battle_hotkeys(self) -> None:
        hotkey = self._hotkey_manager.configuration.battle_rerecord
        self._page.set_rerecord_hotkey_label(hotkey)
        self._hotkey_manager.start(
            owner=self._BATTLE_HOTKEY_OWNER,
            on_battle_rerecord=lambda: self._capture_hotkey_received.emit(
                "battle_rerecord"
            ),
        )

    def _stop_battle_hotkeys(self) -> None:
        self._rerecord_hotkey_armed_until = 0.0
        self._hotkey_manager.stop(owner=self._BATTLE_HOTKEY_OWNER)

    def _handle_capture_hotkey(self, action: str) -> None:
        if (
            action != "battle_rerecord"
            or not self.is_running()
            or self._latest_state.phase != "running"
        ):
            return
        if not capture_entry_allowed(self):
            return
        now = monotonic()
        if (
            self._rerecord_hotkey_armed_until > 0
            and now <= self._rerecord_hotkey_armed_until
        ):
            self.rerecord(confirm=False)
            return
        self._rerecord_hotkey_armed_until = (
            now + self._RERECORD_CONFIRM_SECONDS
        )
        self._page.show_rerecord_hotkey_confirmation(
            self._hotkey_manager.configuration.battle_rerecord,
            self._RERECORD_CONFIRM_SECONDS,
        )

    def rerecord(self, *, confirm: bool = True) -> None:
        service = self._service
        if (
            service is None
            or not self.is_running()
            or self._latest_state.phase != "running"
            or self._restart_pending
        ):
            return
        if not capture_entry_allowed(self):
            return
        if confirm:
            answer = QMessageBox.question(
                self._dialog_parent,
                "放弃当前战报",
                "确定放弃本次尚未保存的战报，并立即重新开始采集吗？",
                (
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                ),
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._restart_pending = True
        self._restart_resume_inventory = self._resume_inventory
        self._rerecord_hotkey_armed_until = 0.0
        self._overlay_capture_active = False
        self._overlay.hide()
        self._stop_battle_hotkeys()
        service.request_discard()

    def _consume_rerecord_terminal(self, state: BattleCaptureState) -> bool:
        restart = (
            self._restart_pending
            and state.phase == "stopped"
            and state.persistence_status == "discarded_restart"
        )
        self._restart_pending = False
        self._rerecord_hotkey_armed_until = 0.0
        if not restart:
            self._restart_resume_inventory = False
        return restart

    def _reset_capture_controls(self) -> None:
        self._stop_battle_hotkeys()
        self._manual_stop_requested = True
        self._restart_pending = False
        self._restart_resume_inventory = False
