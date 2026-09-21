# 从 MainWindow 抽离的控制器方法。
"""Compatibility-installed MainWindow controller."""

from __future__ import annotations

from dataclasses import dataclass
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from src.features.home.page import inventory_sync_error_guidance
from src.observability import OperationContext
from src.services.inventory_sync_service import InventorySyncService, InventorySyncState
from src.integrations.nte_core import NteCoreClient
from src.integrations.nte_core_protocol import NteCoreError
from src.utils.logger import logger
from src.integrations.operation_guard import require_operation


@dataclass(frozen=True)
class InventorySyncNotification:
    account_id: str
    generation: int
    service: object
    run_token: object
    state: InventorySyncState
    manual: bool = False


def invalidate_inventory_sync_notifications(self) -> None:
    """Invalidate queued Qt callbacks before stopping or replacing their owner."""
    self._inventory_sync_state_token = None
    binding = getattr(self, "_inventory_sync_state_binding", None)
    self._inventory_sync_state_binding = None
    if binding is not None:
        service, handler = binding
        service.remove_state_handler(handler)


def _start_inventory_sync(self, *, automatic: bool = False):
    service = self._inventory_sync_service
    if service is not None and service.is_running:
        return
    policy = self.work_mode_service
    source = "native" if policy.allowed("native_sync") else "packet"
    capability = "native_sync" if source == "native" else "packet_capture"
    if not policy.allowed(capability, automatic=automatic):
        if not automatic:
            self.operation_entry("game_sync", "背包同步")
        return
    if policy.settings.paused:
        if not automatic:
            self.operation_unavailable("背包同步", "连接已暂停，请在设置中重新确认工作模式后恢复。", target="detection")
        return
    frozen = self.app_context.account.active_account_id, self.app_context.generation
    try:
        _start_inventory_sync_authorized(self, automatic=automatic, source=source)
    except Exception as error:
        current = self.app_context.account.active_account_id, self.app_context.generation
        if automatic or frozen != current or policy.settings.paused or not policy.allowed(capability):
            logger.warning("背包同步启动未完成：{}", type(error).__name__)
            return
        if isinstance(error, (NteCoreError, OSError)):
            self.operation_unavailable("背包同步", str(error), target="detection")
        else:
            QMessageBox.warning(self, "背包同步", str(error))


def _start_inventory_sync_authorized(self, *, automatic: bool, source: str):
    service=self._inventory_sync_service
    if service is not None and service.is_running:
        return
    invalidate_inventory_sync_notifications(self)
    account = self.app_context.account
    frozen_account_id = account.active_account_id
    frozen_generation = self.app_context.generation
    raw_capture_directory = account.log_dir / "nte_core" / "raw_capture"
    capability = "native_sync" if source == "native" else "packet_capture"

    def guard(requested):
        if requested == capability and not self.work_mode_service.allowed(requested, automatic=automatic):
            raise PermissionError("当前工作模式已停止允许这次背包同步。")
        require_operation(self.operation_guard, requested)

    client_factory = self.native_game_session.inventory_client if source == "native" else lambda: NteCoreClient(
        data_dir=raw_capture_directory, cwd=self.app_context.paths.app_dir, required_source="packet",
    )
    native_profiles_apply = None
    if source == "native":
        from src.services.official_role_profile_service import OfficialRoleProfileService
        native_profiles_apply = OfficialRoleProfileService(
            account.user_database_path, static_database_path=self.app_context.paths.static_database_path,
        ).patch_native_profiles
    service=InventorySyncService(
        account.user_database_path,
        account_id=account.active_account_id,
        account_name=account.active_account_name,
        operation_guard=guard,
        capture_source=source,
        context_is_current=lambda: self.app_context.generation == frozen_generation
        and self.app_context.account.active_account_id == frozen_account_id,
        raw_capture_enabled=source == "packet" and bool(self._get_sync_settings().get("raw_capture_enabled"))
        and self.work_mode_service.allowed("diagnostics"),
        client_factory=client_factory,
        native_profiles_apply=native_profiles_apply,
        raw_capture_directory=raw_capture_directory,
        operation_context=OperationContext.create(
            "inventory_sync",
            account_id=account.active_account_id,
            context_generation=self.app_context.generation,
        ),
    )
    run_token = object()

    def notify(state):
        self.inventory_sync_state_signal.emit(InventorySyncNotification(
            frozen_account_id, frozen_generation, service, run_token, state, not automatic,
        ))

    service.add_state_handler(notify)
    self._inventory_sync_state_token = run_token
    self._inventory_sync_failure_token = None
    self._inventory_sync_guidance_token = run_token if not automatic else None
    self._inventory_sync_state_binding = service, notify
    self._inventory_sync_service=service
    try:
        service.start()
    except Exception:
        invalidate_inventory_sync_notifications(self)
        raise

def _get_sync_settings(self):
    return self._account_settings.load("sync")


def _open_raw_capture_directory(self):
    if not self.work_mode_service.allowed("diagnostics"):
        self.operation_entry("diagnostics", "诊断抓包")
        return
    try:
        require_operation(getattr(self, "operation_guard", None), "diagnostics")
    except PermissionError as error:
        QMessageBox.warning(self, "诊断抓包", str(error))
        return
    directory = (
        self.app_context.account.log_dir / "nte_core" / "raw_capture"
    )
    try:
        directory.mkdir(parents=True, exist_ok=True)
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
    except OSError as exc:
        QMessageBox.warning(self, "诊断抓包", f"无法打开抓包目录：{exc}")
        return
    if not opened:
        QMessageBox.information(self, "诊断抓包", f"抓包目录：\n{directory}")

def _save_capture_diagnostics(self):
    try:
        values=self._account_settings.load("sync")
        if (not bool(values.get("raw_capture_enabled")) and self._sync_raw_capture_toggle.isChecked()
                and not self.operation_entry("diagnostics", "原始抓包诊断")):
            return None
        values.update(
            {
                "capture_device_id":self._sync_capture_device_edit.text(),
                "raw_capture_enabled":self._sync_raw_capture_toggle.isChecked(),
            }
        )
        settings=self._account_settings.save("sync",values)
        return settings
    except Exception as exc:
        QMessageBox.warning(self,"采集排错",f"保存失败：{exc}")
        return None

def _maybe_auto_start_inventory_sync(self):
    self.auto_sync_controller.refresh()

def _stop_inventory_sync(self):
    invalidate_inventory_sync_notifications(self)
    service=self._inventory_sync_service
    if service is None:
        return
    if service.is_running:
        service.stop()
    self._inventory_sync_service=None
    if hasattr(self,"home_sync_badge"):
        from src.ui.dashboard_widgets import set_status_badge
        set_status_badge(self.home_sync_badge,"已停止","neutral")
        self.home_sync_detail.setText("后台背包同步已停止，数据库中的稳定快照仍可用于计算。")
        self.auto_sync_controller.render()

def _on_inventory_sync_state(self, notification):
    if not isinstance(notification, InventorySyncNotification):
        return
    if (notification.service is not self._inventory_sync_service
            or notification.run_token is not getattr(self, "_inventory_sync_state_token", None)
            or notification.generation != self.app_context.generation
            or notification.account_id != self.app_context.account.active_account_id):
        return
    state = notification.state
    if not isinstance(state, InventorySyncState):
        return
    role_revision = (notification.run_token, state.character_sync_revision)
    role_changed = bool(state.character_sync_revision and role_revision != getattr(self, "_native_role_sync_revision", None))
    if role_changed:
        self._native_role_sync_revision = role_revision
        if not getattr(self, "_my_role_dirty", False) and hasattr(self, "_official_role_editors"):
            from src.features.official_role.page import refresh_official_role_page
            refresh_official_role_page(self)
    if state.capturing:
        self._inventory_sync_guidance_token = None
    if (notification.manual and state.phase == "error" and not state.running
            and notification.run_token is getattr(self, "_inventory_sync_guidance_token", None)
            and self.work_mode_service.allowed("native_sync" if state.capture_source == "native" else "packet_capture")
            and not self.work_mode_service.settings.paused
            and notification.run_token is not getattr(self, "_inventory_sync_failure_token", None)
            and state.error_code in {
                "NPCAP_NOT_FOUND", "GAME_PROCESS_NOT_FOUND", "CAPTURE_DEVICE_NOT_FOUND",
                "CAPTURE_START_FAILED", "CAPTURE_FAILED", "PROTOCOL_VERSION_MISMATCH", "HANDSHAKE_REQUIRED",
                "SYSTEM_PROBE_FAILED", "NteCoreNotFoundError",
                "NATIVE_MAPPING_UNSUPPORTED", "NATIVE_CAPABILITY_MISSING",
                "NteCoreProcessError", "NteCoreProtocolError", "NteCoreTimeoutError", "FileNotFoundError",
            }):
        self._inventory_sync_failure_token = notification.run_token
        callback = getattr(self, "operation_unavailable", None)
        if callback is not None:
            native_component_gap = state.error_code in {"NATIVE_MAPPING_UNSUPPORTED", "NATIVE_CAPABILITY_MISSING"}
            detail = state.error if native_component_gap else inventory_sync_error_guidance(
                state.error_code, state.error, capture_source=state.capture_source,
            )
            callback("背包同步", detail, target="deployment" if native_component_gap else "detection")
    # Guidance may navigate and process queued account changes before returning.
    if (notification.service is not self._inventory_sync_service
            or notification.run_token is not getattr(self, "_inventory_sync_state_token", None)
            or notification.account_id != self.app_context.account.active_account_id
            or notification.generation != self.app_context.generation):
        return
    refresh_warehouse = getattr(self, "_on_warehouse_sync_state", None)
    if callable(refresh_warehouse):
        refresh_warehouse(state)
    if not hasattr(self,"home_sync_badge"):
        self.auto_sync_controller.inventory_state_changed(state)
        return
    from src.ui.dashboard_widgets import set_status_badge
    tone={
        "starting":"active","waiting":"warning","collecting":"active",
        "saving":"active","listening":"success","error":"error","stopped":"neutral",
    }.get(state.phase,"neutral")
    label={
        "starting":"启动中","waiting":"等待同步数据" if state.capture_source == "native" else "等待进入游戏","collecting":"接收中",
        "saving":"保存中","listening":"后台监听","error":"同步异常","stopped":"已停止",
    }.get(state.phase,state.phase)
    set_status_badge(self.home_sync_badge,label,tone)
    detail=("DLL 同步 · " if state.capture_source == "native" else "抓包同步 · ")+state.message
    if state.character_sync_error and not hasattr(self, "home_character_sync_detail"):
        detail += "\n" + state.character_sync_error
    if state.pending_item_count is not None:
        detail+=f" · 当前 {state.pending_item_count} 件"
    if state.error:
        detail+=f"\n\n{inventory_sync_error_guidance(state.error_code, state.error, capture_source=state.capture_source)}"
        detail+=f"\n\n技术详情：{state.error}"
    self.home_sync_detail.setText(detail)
    self.auto_sync_controller.inventory_state_changed(state)
    if role_changed or (state.phase=="listening" and state.last_snapshot_id is not None):
        self._refresh_home()

# ── Page: Execute

# ── Page: Equipment

# ── Page: Identify

# ── Page: Blueprint

# ── Page: Config


class InventorySyncControllerMixin:
    invalidate_inventory_sync_notifications = invalidate_inventory_sync_notifications
    _start_inventory_sync = _start_inventory_sync
    _get_sync_settings = _get_sync_settings
    _save_capture_diagnostics = _save_capture_diagnostics
    _open_raw_capture_directory = _open_raw_capture_directory
    _maybe_auto_start_inventory_sync = _maybe_auto_start_inventory_sync
    _stop_inventory_sync = _stop_inventory_sync
    _on_inventory_sync_state = _on_inventory_sync_state
