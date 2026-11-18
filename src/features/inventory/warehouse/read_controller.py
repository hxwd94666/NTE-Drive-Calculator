# 仓库读取只保留一个运行任务和一个最新请求，账号切换撤销投影但不阻塞主线程。
from __future__ import annotations

from src.observability.stage_timings import StageTimings, timed_call

from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
from collections.abc import Callable
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QTimer
from src.ui.common.paint_timings import begin_gui_timing

from src.app.page_tasks import PageCommitLane, PageRequest, PageTaskLane
from src.domain.inventory.warehouse_filter import WarehouseFilterSpec
from src.domain.inventory.warehouse_read_request import WarehouseReadRequest
from src.features.inventory.warehouse.display.presenter import load_warehouse_snapshot
from src.observability import OperationContext
from src.domain.inventory.source_capabilities import is_visual_inventory_source
from src.utils.logger import logger
from src.services.inventory.recommendation.warehouse_auto_quality import read_warehouse_quality


def read_warehouse_request(request: WarehouseReadRequest, *, text_catalog=None) -> dict[str, Any]:
    """后台只消费创建请求时固定的路径；日志身份与读取身份保持一致。"""
    return load_warehouse_snapshot(
        request.database_path,
        OperationContext.create("warehouse", account_id=request.account_id,
                                context_generation=request.generation),
        static_database_path=request.static_database_path,
        asset_root=request.asset_root,
        text_catalog=text_catalog,
    )


class WarehouseReadController(QObject):
    """同一页面跨账号复用同一读取通道，旧 IO 未结束时也不增加线程。"""

    def __init__(self, parent: QObject | None = None, *,
                 reader: Callable[[WarehouseReadRequest], dict[str, Any]] = read_warehouse_request,
                 quality_reader=read_warehouse_quality, text_catalog=None) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._lane = PageTaskLane(self)
        self._reader = reader
        self._quality_reader = quality_reader
        self._quality_lane = PageTaskLane(self)
        self._quality_cancel = Event()
        self.state_save_lane = PageCommitLane(self)
        self.state_save_cancel = Event()
        self._sequence = 0
        self._closed = False
        self.card_timings = None
        self.state_timings = None
        self.read_timings = None
        self.quality_timings = None
        self.recommendation_guard = None
        self.recommendation_targets = {}
        self.manual_targets = {}
        self.draft_revision = 0
        self.base_quality = None
        self.conflicts = []
        self.conflict_guard = None
        self.conflict_button = None
        self.conflict_container = None
        self.management_issues = []
        self.issue_label = None
        self.score_label = None

    def refresh(self, request: WarehouseReadRequest, *,
                apply: Callable[[dict[str, Any]], None], failed: Callable[[str], None]) -> None:
        # 每次刷新有独立键；忙时覆盖等待请求，而不是丢掉更新或复用旧结果。
        self._sequence += 1
        self.cancel_quality()
        if self.read_timings is not None:
            self.read_timings.close()
        timings = self.read_timings = StageTimings(OperationContext.create("warehouse",
            account_id=request.account_id, context_generation=request.generation), foreground=False, slow_ms=2000)
        timings.mark("read_queued")
        reader = self._reader
        def read():
            timings.since("read_queued", "read.queue")
            try:
                return timed_call(timings, "read.snapshot", reader, request)
            finally:
                timings.mark("read_ready")
        def delivered(result):
            timings.since("read_ready", "read.delivery_wait")
            timings.flush("read_received")
            applied = apply(result)
            if applied is False:
                timings.finish("read_unchanged")
            if applied is True and request.config_dir is not None and request.scoring_database_path is not None:
                self._start_quality(request,result)
        self._lane.submit(PageRequest(
            key=(request.account_id, request.generation, self._sequence),
            # 只冻结读取函数与请求；不要让待删除的子线程反向持有 QObject owner。
            # 取消后的延迟析构否则可能释放最后父引用，重入父子对象的删除。
            read=read, apply=delivered, failed=lambda error: (timings.finish("read_failed", outcome="failed"), failed(error)),
        ))

    def cancel_quality(self):
        if self.quality_timings is not None:
            self.quality_timings.close()
        self._quality_cancel.set()
        self._quality_lane.cancel()

    def _start_quality(self, request, result):
        if self._closed or self.score_label is None:
            return
        snapshot_id = result.get('snapshot_id')
        if snapshot_id is None or not result.get('items'):
            self.score_label.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.start_quality.score_label_2'))
            self.score_label.setToolTip('')
            return
        self.score_label.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('warehouse.page.page_warehouse.score_label'))
        self.score_label.setToolTip(plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.start_quality.score_label')))
        self._quality_cancel = Event()
        token = self._quality_cancel
        timings = self.quality_timings = StageTimings(OperationContext.create("warehouse",
            account_id=request.account_id, context_generation=request.generation, snapshot_id=snapshot_id), foreground=False)
        timings.mark("quality_queued")
        reader = self._quality_reader
        def read():
            timings.since("quality_queued", "quality.queue")
            try:
                options = {"timings": timings} if reader is read_warehouse_quality else {}
                return timed_call(timings, "quality.total", reader, request, snapshot_id, token.is_set, **options)
            finally:
                timings.mark("quality_ready")
        # 工作线程仅持有函数、冻结请求及本次取消标记；不读取 QWidget 或变化中的账号。
        self._quality_lane.submit(PageRequest(
            (request.account_id,request.generation,self._sequence),
            read,
            self._quality_ready,self._quality_failed))

    def _quality_ready(self, quality):
        if self.quality_timings is not None:
            self.quality_timings.since("quality_ready", "quality.delivery_wait")
            self.quality_timings.finish("quality_received")
        value = quality.get('score')
        self.score_label.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.quality_ready.score_label') if value is None else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.quality_ready.score_label_2', value=f'{value:.2f}'))
        self.score_label.setToolTip(plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.quality_ready.score_label_3', value=f"{quality['core_count']}", value_2=f"{quality['core_target']}", value_3=f"{quality['drive_count']}")))
        self.base_quality = (self.score_label.text(), self.score_label.toolTip())

    def _quality_failed(self, message):
        if self.quality_timings is not None:
            self.quality_timings.finish("quality_failed", outcome="failed")
        self.score_label.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.inventory.quality_failed.score_label'))
        self.score_label.setToolTip(message)

    def cancel(self) -> None:
        """账号变更先失效结果；物理读取由原通道持有至结束。"""
        self._lane.cancel()
        self.state_save_cancel.set()
        for timings in (self.card_timings, self.state_timings, self.read_timings):
            if timings is not None:
                timings.close()
        self.card_timings = self.state_timings = self.read_timings = None
        self.cancel_quality()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        self._closed = True
        self.cancel()
        self._lane.close()
        self.cancel_quality()
        self._quality_lane.close()

    def is_running(self) -> bool:
        return self._lane.is_running() or self._quality_lane.is_running() or self.state_save_lane.is_running()


def reset_warehouse_account(host: Any, *, text_catalog=None) -> None:
    """旧账号展示与草稿一起清除，隐藏页也不残留可操作的旧 UID。"""
    owner = getattr(host, "warehouse_read_controller", None)
    if owner is None:
        return
    owner.cancel()
    from src.features.inventory.warehouse.progress import close_warehouse_state_progress
    close_warehouse_state_progress(host)
    host._warehouse_management_busy = False
    owner.draft_revision += 1
    owner.base_quality = None
    owner.recommendation_guard = None
    owner.recommendation_targets = {}
    owner.manual_targets = {}
    owner.conflicts = []
    owner.conflict_guard = None
    owner.management_issues = []
    if owner.conflict_button is not None:
        owner.conflict_button.hide()
    summary = getattr(owner, 'conflict_summary_container', None)
    if summary is not None:
        summary.hide()
    if owner.score_label is not None:
        owner.score_label.setText(resolve_text_catalog(text_catalog).render('ui.inventory.reset_warehouse_account.score_label'))
        owner.score_label.setToolTip('')
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
    host._update_warehouse_save_state()
    host.warehouse_source_notice.hide()


def apply_warehouse_snapshot(host: Any, result: dict[str, Any], *, text_catalog=None) -> bool:
    """只由当前读取请求进入；保留原有筛选、卡片及来源提示行为。"""
    # 发起时无草稿不代表交付时无草稿；不覆盖读取期间新发生的编辑或状态操作。
    if warehouse_projection_busy(host):
        defer_warehouse_refresh(host, result.get("snapshot_id"), text_catalog=text_catalog)
        return False
    confirmation_after = getattr(host, "_warehouse_confirmation_after", None)
    snapshot_id = result.get("snapshot_id")
    if isinstance(confirmation_after, int) and (not isinstance(snapshot_id, int) or snapshot_id <= confirmation_after):
        # RPC 接受后的待确认面板不能被写入前的快照覆盖；不因此自动重发读/写。
        host._warehouse_refresh_deferred = False
        host.warehouse_hint.setText(resolve_text_catalog(text_catalog).render('ui.inventory.apply_warehouse_snapshot.warehouse_hint'))
        host.warehouse_hint.show()
        return False
    host._warehouse_confirmation_after = None
    host._warehouse_refresh_deferred = False
    owner = host.warehouse_read_controller
    timings = owner.read_timings
    identity = host.app_context.account.active_account_id, host.app_context.generation
    probe = begin_gui_timing(host.warehouse_view.viewport(), timings,
        is_current=lambda: not owner.closed and identity ==
            (host.app_context.account.active_account_id, host.app_context.generation))
    if host._warehouse_snapshot_id != snapshot_id:
        owner.draft_revision += 1
        owner.base_quality = None
        owner.recommendation_guard = None
        owner.recommendation_targets = {}
        owner.manual_targets = {}
        owner.conflicts = []
        owner.conflict_guard = None
        owner.management_issues = []
        if owner.score_label is not None:
            owner.score_label.setText(resolve_text_catalog(text_catalog).render('ui.inventory.apply_warehouse_snapshot.score_label'))
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
    host._apply_warehouse_filters(timings=timings)
    if probe is not None:
        probe.submitted()
    if timings is not None:
        timings.flush("read_gui_submitted")
    if is_visual_inventory_source(host._warehouse_source):
        host.warehouse_source_notice.setText(resolve_text_catalog(text_catalog).render('ui.inventory.apply_warehouse_snapshot.warehouse_source_notice'))
        host.warehouse_source_notice.show()
    else:
        host.warehouse_source_notice.hide()
    return True


def show_warehouse_read_error(host: Any, error: str, *, text_catalog=None) -> None:
    # 同账号刷新失败不清掉上次有效面板；换账号已在生命周期钩子中先清空。
    host._apply_warehouse_filters()
    if host._warehouse_snapshot_id is None:
        host.warehouse_summary.setText(resolve_text_catalog(text_catalog).render('ui.inventory.show_warehouse_read_error.warehouse_summary'))
    host.warehouse_hint.setText(resolve_text_catalog(text_catalog).render('ui.inventory.show_warehouse_read_error.warehouse_hint', error=f'{error}'))
    host.warehouse_hint.show()
    if host.warehouse_read_controller.score_label is not None:
        host.warehouse_read_controller.score_label.setText(resolve_text_catalog(text_catalog).render('ui.inventory.show_warehouse_read_error.score_label'))
        host.warehouse_read_controller.score_label.setToolTip(error)
    logger.error(f"读取仓库稳定快照失败: {error}")


def warehouse_projection_busy(host: Any) -> bool:
    """草稿或尚未交付的状态操作拥有面板；结果交付后不误判线程收尾为业务忙。"""
    if getattr(host, "_warehouse_pending_state_changes", {}):
        return True
    busy = getattr(host, "_warehouse_management_busy", None)
    if busy is not None:
        return bool(busy)
    lane = getattr(getattr(host, "warehouse_read_controller", None), "state_save_lane", None)
    return lane is not None and lane.is_running()


def defer_warehouse_refresh(host: Any, snapshot_id: int | None = None, *, text_catalog=None) -> None:
    host._warehouse_refresh_deferred = True
    previous = getattr(host, "_warehouse_deferred_snapshot_id", None)
    if isinstance(snapshot_id, int):
        host._warehouse_deferred_snapshot_id = max(snapshot_id, previous if isinstance(previous, int) else snapshot_id)
    host.warehouse_hint.setText(resolve_text_catalog(text_catalog).render('ui.inventory.defer_warehouse_refresh.warehouse_hint'))
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
