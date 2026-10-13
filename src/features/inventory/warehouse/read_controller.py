# 仓库读取只保留一个运行任务和一个最新请求，账号切换撤销投影但不阻塞主线程。
from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

from PySide6.QtCore import QObject, QTimer

from src.app.page_tasks import PageRequest, PageTaskLane
from src.domain.inventory.warehouse_filter import WarehouseFilterSpec
from src.domain.inventory.warehouse_read_request import WarehouseReadRequest
from src.features.inventory.warehouse.presenter import load_warehouse_snapshot
from src.observability import OperationContext
from src.domain.inventory.source_capabilities import is_visual_inventory_source
from src.utils.logger import logger


def read_warehouse_request(request: WarehouseReadRequest) -> dict[str, Any]:
    """后台只消费创建请求时固定的路径；日志身份与读取身份保持一致。"""
    return load_warehouse_snapshot(
        request.database_path,
        OperationContext.create("warehouse", account_id=request.account_id,
                                context_generation=request.generation),
        static_database_path=request.static_database_path,
        asset_root=request.asset_root,
    )


class WarehouseReadController(QObject):
    """同一页面跨账号复用同一读取通道，旧 IO 未结束时也不增加线程。"""

    def __init__(self, parent: QObject | None = None, *,
                 reader: Callable[[WarehouseReadRequest], dict[str, Any]] = read_warehouse_request) -> None:
        super().__init__(parent)
        self._lane = PageTaskLane(self)
        self._reader = reader
        self._sequence = 0
        self._closed = False

    def refresh(self, request: WarehouseReadRequest, *,
                apply: Callable[[dict[str, Any]], None], failed: Callable[[str], None]) -> None:
        # 每次刷新有独立键；忙时覆盖等待请求，而不是丢掉更新或复用旧结果。
        self._sequence += 1
        self._lane.submit(PageRequest(
            key=(request.account_id, request.generation, self._sequence),
            # 只冻结读取函数与请求；不要让待删除的子线程反向持有 QObject owner。
            # 取消后的延迟析构否则可能释放最后父引用，重入父子对象的删除。
            read=partial(self._reader, request), apply=apply, failed=failed,
        ))

    def cancel(self) -> None:
        """账号变更先失效结果；物理读取由原通道持有至结束。"""
        self._lane.cancel()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True
        self._lane.close()

    def is_running(self) -> bool:
        return self._lane.is_running()


def reset_warehouse_account(host: Any) -> None:
    """旧账号展示与草稿一起清除，隐藏页也不残留可操作的旧 UID。"""
    owner = getattr(host, "warehouse_read_controller", None)
    if owner is None:
        return
    owner.cancel()
    host._warehouse_refresh_deferred = False
    host._warehouse_confirmation_after = None
    host._warehouse_snapshot_id = None
    host._warehouse_source = None
    host._warehouse_all_items = []
    host._warehouse_pending_state_changes = {}
    host._warehouse_base_states = {}
    host._warehouse_compare_first = None
    host._warehouse_deferred_snapshot_id = None
    host._warehouse_filter_spec = WarehouseFilterSpec()
    host.warehouse_filter_drawer.set_items([], host._warehouse_filter_spec)
    host._apply_warehouse_filters()
    host.warehouse_source_notice.hide()


def apply_warehouse_snapshot(host: Any, result: dict[str, Any]) -> None:
    """只由当前读取请求进入；保留原有筛选、卡片及来源提示行为。"""
    # 发起时无草稿不代表交付时无草稿；不覆盖读取期间新发生的编辑或状态操作。
    if warehouse_projection_busy(host):
        defer_warehouse_refresh(host, result.get("snapshot_id"))
        return
    confirmation_after = getattr(host, "_warehouse_confirmation_after", None)
    snapshot_id = result.get("snapshot_id")
    if isinstance(confirmation_after, int) and (not isinstance(snapshot_id, int) or snapshot_id <= confirmation_after):
        # RPC 接受后的待确认面板不能被写入前的快照覆盖；不因此自动重发读/写。
        host._warehouse_refresh_deferred = False
        host.warehouse_hint.setText("修改已提交，等待后续稳定快照确认。")
        host.warehouse_hint.show()
        return
    host._warehouse_confirmation_after = None
    host._warehouse_refresh_deferred = False
    host._warehouse_snapshot_id = snapshot_id
    deferred = getattr(host, "_warehouse_deferred_snapshot_id", None)
    if isinstance(deferred, int) and isinstance(host._warehouse_snapshot_id, int) and host._warehouse_snapshot_id >= deferred:
        host._warehouse_deferred_snapshot_id = None
    host._warehouse_source = str(result.get("source") or "")
    host._warehouse_all_items = list(result.get("items") or [])
    host._warehouse_pending_state_changes = {}
    host._warehouse_base_states = {
        str(item.get("uid")): "discarded" if item.get("discarded") else "locked" if item.get("locked") else "normal"
        for item in host._warehouse_all_items if item.get("state_known", True)
    }
    host._warehouse_compare_first = None
    host._warehouse_filter_spec = host.warehouse_filter_drawer.set_items(
        host._warehouse_all_items, getattr(host, "_warehouse_filter_spec", WarehouseFilterSpec()),
    )
    host._apply_warehouse_filters()
    if is_visual_inventory_source(host._warehouse_source):
        host.warehouse_source_notice.setText("视觉扫描库存：等级、锁定/弃置和已装备角色未知；鉴定与对比仍可用。")
        host.warehouse_source_notice.show()
    else:
        host.warehouse_source_notice.hide()


def show_warehouse_read_error(host: Any, error: str) -> None:
    # 同账号刷新失败不清掉上次有效面板；换账号已在生命周期钩子中先清空。
    host._apply_warehouse_filters()
    if host._warehouse_snapshot_id is None:
        host.warehouse_summary.setText("读取失败")
    host.warehouse_hint.setText(f"仓库读取失败：{error}；请重试。")
    host.warehouse_hint.show()
    logger.error(f"读取仓库稳定快照失败: {error}")


def warehouse_projection_busy(host: Any) -> bool:
    """草稿或尚未交付的状态操作拥有面板；结果交付后不误判线程收尾为业务忙。"""
    if getattr(host, "_warehouse_pending_state_changes", {}):
        return True
    busy = getattr(host, "_warehouse_management_busy", None)
    if busy is not None:
        return bool(busy)
    worker = getattr(host, "_warehouse_state_worker", None)
    return worker is not None and worker.isRunning()


def defer_warehouse_refresh(host: Any, snapshot_id: int | None = None) -> None:
    host._warehouse_refresh_deferred = True
    previous = getattr(host, "_warehouse_deferred_snapshot_id", None)
    if isinstance(snapshot_id, int):
        host._warehouse_deferred_snapshot_id = max(snapshot_id, previous if isinstance(previous, int) else snapshot_id)
    host.warehouse_hint.setText("背包有待刷新内容；当前修改结束后将自动刷新。")
    host.warehouse_hint.show()


def refresh_warehouse_if_ready(host: Any) -> None:
    """撤销草稿或交付状态结果后只排一个刷新；转场或关闭会撤销此回调。"""
    owner = getattr(host, "warehouse_read_controller", None)
    if owner is None or owner.closed or not getattr(host, "_warehouse_refresh_deferred", False) or warehouse_projection_busy(host):
        return
    confirmation_after = getattr(host, "_warehouse_confirmation_after", None)
    deferred = getattr(host, "_warehouse_deferred_snapshot_id", None)
    if isinstance(confirmation_after, int) and (not isinstance(deferred, int) or deferred <= confirmation_after):
        return
    context = host.app_context
    identity = context.account.active_account_id, context.generation
    host._warehouse_refresh_deferred = False

    def refresh_current() -> None:
        if not owner.closed and (context.account.active_account_id, context.generation) == identity:
            host._refresh_warehouse()

    # 使用 QObject 上下文，销毁页面后 Qt 不再投递；不在提交回调中嵌套发起读取。
    QTimer.singleShot(0, owner, refresh_current)
