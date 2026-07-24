# 从 MainWindow 抽离的控制器方法。
"""Compatibility-installed MainWindow controller."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from src.app import runtime
from src.app.workers import WorkerThread
from src.features.home.page import inventory_sync_error_guidance
from src.services.inventory_sync_service import InventorySyncService, InventorySyncState
from src.storage.sqlite.user_data_dao import UserDataDao
from src.ui.main_window_method_install import install_methods as _install_main_window_methods
from src.utils.logger import logger

_METHOD_NAMES = ["_start_inventory_sync","_get_sync_settings","_save_sync_settings","_prune_inventory_snapshots","_prune_inventory_snapshots_task","_on_inventory_snapshots_pruned","_on_inventory_snapshot_prune_error","_maybe_auto_start_inventory_sync","_stop_inventory_sync","_on_inventory_sync_state"]


def install_methods(app_module, window_cls) -> None:
    _install_main_window_methods(app_module, window_cls, _METHOD_NAMES, globals())


def _start_inventory_sync(self):
    service=self._inventory_sync_service
    if service is not None and service.is_running:
        return
    service=InventorySyncService(runtime.USER_DATABASE_PATH)
    service.add_state_handler(self.inventory_sync_state_signal.emit)
    self._inventory_sync_service=service
    service.start()

def _get_sync_settings(self):
    return self._account_settings.load("sync")

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
        QMessageBox.information(self,"同步设置","同步设置已保存。")
        return settings
    except Exception as exc:
        QMessageBox.warning(self,"同步设置",f"保存失败：{exc}")
        return None

def _prune_inventory_snapshots(self):
    current_worker = getattr(self, "_snapshot_prune_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "快照维护", "历史快照正在清理，请等待当前任务完成。")
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
        "确认清理历史快照",
        message,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    ) != QMessageBox.Yes:
        return

    database_path = runtime.USER_DATABASE_PATH
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

@staticmethod
def _prune_inventory_snapshots_task(database_path, retain_recent):
    with UserDataDao(database_path) as dao:
        return dao.prune_inventory_snapshots(retain_recent=retain_recent)

def _on_inventory_snapshots_pruned(self, result):
    if hasattr(self, "_prune_snapshots_button"):
        self._prune_snapshots_button.setEnabled(True)
    self._refresh_home()
    QMessageBox.information(
        self,
        "快照维护完成",
        "已清理 "
        f"{result['deleted_snapshot_count']} 份历史快照，"
        f"当前保留 {result['total_after']} 份。\n\n"
        "当前快照和被装配方案引用的快照未被删除。"
        "SQLite 数据库文件大小可能不会立刻缩小，但空间会供后续同步复用。",
    )

def _on_inventory_snapshot_prune_error(self, error):
    if hasattr(self, "_prune_snapshots_button"):
        self._prune_snapshots_button.setEnabled(True)
    QMessageBox.warning(self, "快照维护", f"清理失败：{error}")

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
        set_status_badge(self.home_sync_badge,"已停止","neutral")
        self.home_sync_detail.setText("后台背包同步已停止，数据库中的稳定快照仍可用于计算。")
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
        "starting":"启动中","waiting":"等待进入游戏","collecting":"接收中",
        "saving":"保存中","listening":"后台监听","error":"同步异常","stopped":"已停止",
    }.get(state.phase,state.phase)
    set_status_badge(self.home_sync_badge,label,tone)
    detail=state.message
    if state.pending_item_count is not None:
        detail+=f" · 当前 {state.pending_item_count} 件"
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
