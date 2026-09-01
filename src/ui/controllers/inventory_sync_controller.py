# 从 MainWindow 抽离的控制器方法。
"""Compatibility-installed MainWindow controller."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from src.i18n import tr
from src.app.workers import WorkerThread
from src.features.home.page import inventory_sync_error_guidance
from src.observability import OperationContext
from src.services.inventory_sync_service import InventorySyncService, InventorySyncState
from src.integrations.nte_core import NteCoreClient
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger


def _start_inventory_sync(self):
    service=self._inventory_sync_service
    if service is not None and service.is_running:
        return
    account = self.app_context.account
    raw_capture_directory = account.log_dir / "nte_core" / "raw_capture"
    service=InventorySyncService(
        account.user_database_path,
        account_id=account.active_account_id,
        account_name=account.active_account_name,
        client_factory=lambda: NteCoreClient(
            data_dir=raw_capture_directory,
            cwd=self.app_context.paths.app_dir,
        ),
        raw_capture_directory=raw_capture_directory,
        operation_context=OperationContext.create(
            "inventory_sync",
            account_id=account.active_account_id,
            context_generation=self.app_context.generation,
        ),
    )
    service.add_state_handler(self.inventory_sync_state_signal.emit)
    self._inventory_sync_service=service
    service.start()

def _get_sync_settings(self):
    return self._account_settings.load("sync")


def _open_raw_capture_directory(self):
    directory = (
        self.app_context.account.log_dir / "nte_core" / "raw_capture"
    )
    try:
        directory.mkdir(parents=True, exist_ok=True)
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
    except OSError as exc:
        QMessageBox.warning(self, tr("诊断抓包"), tr("无法打开抓包目录：{error}", error=exc))
        return
    if not opened:
        QMessageBox.information(self, tr("诊断抓包"), tr("抓包目录：\n{path}", path=directory))

def _save_sync_settings(self):
    try:
        was_running=bool(self._inventory_sync_service and self._inventory_sync_service.is_running)
        values=self._account_settings.load("sync")
        values.update(
            {
                "inventory_sync_method":self._sync_inventory_method_combo.currentData(),
                "capture_device_id":self._sync_capture_device_edit.text(),
                "raw_capture_enabled":self._sync_raw_capture_toggle.isChecked(),
                "inventory_settle_seconds":self._sync_settle_spin.value(),
                "auto_start_inventory_sync":self._sync_auto_start_toggle.isChecked(),
                "inventory_snapshot_retention_count":self._snapshot_retention_spin.value(),
            }
        )
        settings=self._account_settings.save("sync",values)
        if was_running:
            self._stop_inventory_sync()
            self._start_inventory_sync()
        QMessageBox.information(self,tr("同步设置"),tr("同步设置已保存。"))
        return settings
    except Exception as exc:
        QMessageBox.warning(self, tr("同步设置"), tr("保存失败：{error}", error=exc))
        return None

def _prune_inventory_snapshots(self):
    current_worker = getattr(self, "_snapshot_prune_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, tr("快照维护"), tr("历史快照正在清理，请等待当前任务完成。"))
        return
    retain_recent = self._snapshot_retention_spin.value()
    message = (
        f"将保留最近 {retain_recent} 份稳定背包快照。\n\n"
        "当前快照和所有已保存装配方案引用的快照会始终保留；"
        "其他历史快照及其背包物品、词条记录将被删除。\n\n"
        "此操作不会修改装配方案。是否继续？"
    )
    if QMessageBox.question(
        self,
        tr("确认清理历史快照"),
        message,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    ) != QMessageBox.Yes:
        return

    database_path = self.app_context.account.user_database_path
    if hasattr(self, "_prune_snapshots_button"):
        self._prune_snapshots_button.setEnabled(False)
    worker = WorkerThread(
        target=lambda: self._prune_inventory_snapshots_task(
            database_path, retain_recent
        ),
        parent=self,
    )
    self._snapshot_prune_worker = worker
    worker.result_ready.connect(self._on_inventory_snapshots_pruned)
    worker.error.connect(self._on_inventory_snapshot_prune_error)
    worker.start()

def _prune_inventory_snapshots_task(database_path, retain_recent):
    with UserDataDao(database_path) as dao:
        return dao.prune_inventory_snapshots(retain_recent=retain_recent)

def _on_inventory_snapshots_pruned(self, result):
    if hasattr(self, "_prune_snapshots_button"):
        self._prune_snapshots_button.setEnabled(True)
    self._refresh_home()
    QMessageBox.information(
        self,
        tr("快照维护完成"),
        tr("已清理 {deleted} 份历史快照，当前保留 {kept} 份。\n\n"
           "当前快照和被装配方案引用的快照未被删除。"
           "SQLite 数据库文件大小可能不会立刻缩小，但空间会供后续同步复用。",
           deleted=result["deleted_snapshot_count"], kept=result["total_after"]),
    )

def _on_inventory_snapshot_prune_error(self, error):
    if hasattr(self, "_prune_snapshots_button"):
        self._prune_snapshots_button.setEnabled(True)
    QMessageBox.warning(self, tr("快照维护"), tr("清理失败：{error}", error=error))

def _maybe_auto_start_inventory_sync(self):
    try:
        settings=self._get_sync_settings()
    except Exception as exc:
        logger.debug(f"读取自动同步设置失败: {exc}")
        return
    if (
        settings.get("inventory_sync_method")=="nte_core"
        and settings.get("auto_start_inventory_sync")
    ):
        self._start_inventory_sync()

def _stop_inventory_sync(self):
    service=self._inventory_sync_service
    if service is None:
        return
    service.remove_state_handler(self.inventory_sync_state_signal.emit)
    if service.is_running:
        service.stop()
    self._inventory_sync_service=None
    if hasattr(self,"home_sync_badge"):
        from src.ui.dashboard_widgets import set_status_badge
        set_status_badge(self.home_sync_badge, tr("已停止"), "neutral")
        self.home_sync_detail.setText(tr("后台背包同步已停止，数据库中的稳定快照仍可用于计算。"))
        self.home_start_sync_button.setEnabled(True)
        self.home_stop_sync_button.setEnabled(False)

def _on_inventory_sync_state(self,state):
    if not isinstance(state,InventorySyncState):
        return
    refresh_warehouse = getattr(self, "_on_warehouse_sync_state", None)
    if callable(refresh_warehouse):
        refresh_warehouse(state)
    if not hasattr(self,"home_sync_badge"):
        return
    from src.ui.dashboard_widgets import set_status_badge
    tone={
        "starting":"active","waiting":"warning","collecting":"active",
        "saving":"active","listening":"success","error":"error","stopped":"neutral",
    }.get(state.phase,"neutral")
    label={
        "starting": tr("启动中"), "waiting": tr("等待进入游戏"), "collecting": tr("接收中"),
        "saving": tr("保存中"), "listening": tr("后台监听"),
        "error": tr("同步异常"), "stopped": tr("已停止"),
    }.get(state.phase,state.phase)
    set_status_badge(self.home_sync_badge,label,tone)
    detail=tr(state.message)
    if state.pending_item_count is not None:
        detail+=tr(" · 当前 {count} 件", count=state.pending_item_count)
    if state.error:
        detail+=f"\n\n{inventory_sync_error_guidance(state.error_code, state.error)}"
        detail+=f"\n\n技术详情：{state.error}"
    self.home_sync_detail.setText(detail)
    self.home_start_sync_button.setEnabled(not state.running)
    self.home_stop_sync_button.setEnabled(state.running)
    self.status_lbl.setText(label)
    self.status_lbl.setStyleSheet(
        "color:#f85149;font-size:12px" if state.phase=="error"
        else "color:#3fb950;font-size:12px" if state.phase=="listening"
        else "color:#d2991d;font-size:12px"
    )
    if state.phase=="listening" and state.last_snapshot_id is not None:
        self._refresh_home()

# ── Page: Execute

# ── Page: Equipment

# ── Page: Identify

# ── Page: Blueprint

# ── Page: Config


class InventorySyncControllerMixin:
    _start_inventory_sync = _start_inventory_sync
    _get_sync_settings = _get_sync_settings
    _save_sync_settings = _save_sync_settings
    _open_raw_capture_directory = _open_raw_capture_directory
    _prune_inventory_snapshots = _prune_inventory_snapshots
    _prune_inventory_snapshots_task = staticmethod(
        _prune_inventory_snapshots_task
    )
    _on_inventory_snapshots_pruned = _on_inventory_snapshots_pruned
    _on_inventory_snapshot_prune_error = _on_inventory_snapshot_prune_error
    _maybe_auto_start_inventory_sync = _maybe_auto_start_inventory_sync
    _stop_inventory_sync = _stop_inventory_sync
    _on_inventory_sync_state = _on_inventory_sync_state
