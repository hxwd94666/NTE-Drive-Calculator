# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

from src.ui.common.enum_text import ui_enum_text
from src.domain.localization.catalog import localized_text
from src.observability.stage_timings import StageTimings, timed_call

from src.ui.common.text_catalog import TextActionButton, plain_label, plain_tooltip, resolve_text_catalog
from src.features.input_operation_entry import request_input_entry, show_sync_required
from collections.abc import Mapping
from concurrent.futures import CancelledError
from threading import Event
from typing import Any
from src.ui.common.paint_timings import begin_gui_timing

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.domain.inventory.warehouse_filter import WarehouseFilterSpec
from src.features.inventory.warehouse.display.cards import (
    WarehouseCardDelegate,
    WarehouseGridView,
    WarehouseInventoryModel,
    filter_warehouse_items,
)
from src.features.inventory.warehouse.display.item_state import warehouse_item_with_state
from src.features.inventory.warehouse.draft_actions import clear_warehouse_draft
from src.features.inventory.warehouse.management.management_projection import refresh_conflict_summary
from src.features.inventory.warehouse.display.filter_drawer import WarehouseFilterDrawer
from src.domain.inventory.warehouse_read_request import WarehouseReadRequest
from src.features.inventory.warehouse.read_controller import (
    WarehouseReadController, apply_warehouse_snapshot, show_warehouse_read_error,
    defer_warehouse_refresh, warehouse_projection_busy,
)
from src.features.inventory.warehouse.progress import (
    close_warehouse_state_progress,
    on_warehouse_state_error as _on_warehouse_state_error,
    set_warehouse_management_busy as _set_warehouse_management_busy,
    show_warehouse_state_progress,
    update_warehouse_save_state as _update_warehouse_save_state,
)
from src.features.inventory.warehouse.identification_controller import (
    select_warehouse_compare_item as _select_warehouse_compare_item,
    show_warehouse_item_identification as _show_warehouse_item_identification,
)
from src.observability import OperationContext
from src.services.inventory.management.warehouse_state_management import WarehouseStateManagementService


def _new_warehouse_timings(host, *, foreground=True, slow_ms=10_000):
    context = host.app_context
    return StageTimings(OperationContext.create("warehouse", account_id=context.account.active_account_id,
        context_generation=context.generation, snapshot_id=host._warehouse_snapshot_id),
        foreground=foreground, slow_ms=slow_ms)


def _warehouse_timed_worker(timings, phase, function):
    if timings is None:
        return function()
    timings.since(phase + ".queued", phase + ".queue")
    try:
        return timed_call(timings, phase, function)
    finally:
        timings.mark(phase + ".ready")


def _card_timing(host):
    owner = host.warehouse_read_controller
    if owner.card_timings is not None:
        owner.card_timings.close()
    timings = owner.card_timings = _new_warehouse_timings(host, foreground=False, slow_ms=250)
    identity = host.app_context.account.active_account_id, host.app_context.generation
    probe = begin_gui_timing(host.warehouse_view.viewport(), timings,
        is_current=lambda: (not owner.closed and identity ==
                            (host.app_context.account.active_account_id, host.app_context.generation)))
    return timings, probe


def _finish_card_timing(timings, probe):
    if probe is not None:
        probe.submitted()
    timings.flush("card_submitted")


def _page_warehouse(self):
    """Create the virtualized official-inventory page without loading items yet."""
    _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
    page = QWidget()
    self.warehouse_read_controller = WarehouseReadController(page, text_catalog=getattr(self, 'text_catalog', None))
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)

    title_row = QHBoxLayout()
    self.warehouse_summary = plain_label(_ui_text('warehouse.page.page_warehouse.warehouse_summary'))
    self.warehouse_summary.setStyleSheet(themed_style("color:#8b949e;margin-left:8px"))
    title_row.addWidget(self.warehouse_summary)
    self.warehouse_selection_label = plain_label(_ui_text('warehouse.page.page_warehouse.warehouse_selection_label'))
    self.warehouse_selection_label.setStyleSheet(themed_style("color:#8b949e;margin-left:8px"))
    title_row.addWidget(self.warehouse_selection_label)
    multi_select_hint = plain_label(_ui_text('warehouse.page.page_warehouse.multi_select_hint'))
    multi_select_hint.setStyleSheet(themed_style("color:#8b949e"))
    title_row.addWidget(multi_select_hint)
    self.warehouse_normal_btn = QPushButton(_ui_text('warehouse.page.page_warehouse.warehouse_normal_btn'))
    self.warehouse_lock_btn = QPushButton(_ui_text('warehouse.page.page_warehouse.warehouse_lock_btn'))
    self.warehouse_discard_btn = QPushButton(_ui_text('warehouse.page.page_warehouse.warehouse_discard_btn'))
    for button, target_state in (
        (self.warehouse_normal_btn, "normal"),
        (self.warehouse_lock_btn, "locked"),
        (self.warehouse_discard_btn, "discarded"),
    ):
        button.setObjectName("btnAction")
        button.setEnabled(False)
        button.clicked.connect(
            lambda _checked=False, target=target_state: self._set_warehouse_selected_state(target)
        )
        title_row.addWidget(button)
    title_row.addStretch()
    self.warehouse_manage_btn = QPushButton(_ui_text('warehouse.page.page_warehouse.warehouse_manage_btn'))
    self.warehouse_manage_btn.setObjectName("btnPrimary")
    self.warehouse_manage_btn.setToolTip(plain_tooltip(self.text_catalog.render('warehouse.management.manage_hint')))
    self.warehouse_manage_btn.clicked.connect(self._open_warehouse_state_manager)
    title_row.addWidget(self.warehouse_manage_btn)
    self.warehouse_clear_btn = TextActionButton(self.text_catalog.render("warehouse.draft.clear"))
    self.warehouse_clear_btn.setObjectName("btnDanger")
    self.warehouse_clear_btn.setToolTip(plain_tooltip(self.text_catalog.render("warehouse.draft.clear_hint")))
    self.warehouse_clear_btn.setEnabled(False)
    self.warehouse_clear_btn.clicked.connect(lambda: clear_warehouse_draft(self))
    title_row.addWidget(self.warehouse_clear_btn)
    self.warehouse_save_btn = QPushButton(_ui_text('warehouse.page.page_warehouse.warehouse_save_btn'))
    self.warehouse_save_btn.setObjectName("btnPrimary")
    self.warehouse_save_btn.setStyleSheet(
        themed_style(
            "QPushButton{background:#1f6feb;border-color:#388bfd;color:white;}"
            "QPushButton:hover{background:#388bfd;}"
            "QPushButton:disabled{background:#30363d;color:#8b949e;}"
        )
    )
    self.warehouse_save_btn.setToolTip(plain_tooltip(self.text_catalog.render('warehouse.management.save_hint')))
    self.warehouse_save_btn.setEnabled(True)
    self.warehouse_save_btn.clicked.connect(self._save_warehouse_state_changes)
    title_row.addWidget(self.warehouse_save_btn)
    layout.addLayout(title_row)
    self.warehouse_read_controller.score_label = getattr(
        self, 'topbar_warehouse_score', plain_label('', page)
    )
    self.warehouse_read_controller.score_label.setText(_ui_text('warehouse.page.page_warehouse.score_label'))
    conflict_button = getattr(self, 'topbar_warehouse_conflict_details', None)
    owner = self.warehouse_read_controller
    owner.conflict_button = conflict_button
    owner.conflict_container = None
    owner.conflict_summary_label = getattr(self, 'topbar_warehouse_conflict_summary', None)
    owner.conflict_summary_container = getattr(self, 'topbar_warehouse_conflict_container', None)
    if conflict_button is not None:
        conflict_button.clicked.connect(lambda: self.warehouse_management_controller.open_conflicts(owner.conflicts, owner.conflict_guard))
    filters = QHBoxLayout()
    filters.setSpacing(8)
    self.warehouse_search = QLineEdit()
    self.warehouse_search.setPlaceholderText(_ui_text('warehouse.page.page_warehouse.warehouse_search.placeholder'))
    self.warehouse_search.setClearButtonEnabled(True)
    self.warehouse_search.setMinimumWidth(280)
    self.warehouse_search.textChanged.connect(self._apply_warehouse_filters)
    filters.addWidget(self.warehouse_search, 1)
    self.warehouse_filter_btn = QPushButton(_ui_text('warehouse.page.page_warehouse.warehouse_filter_btn'))
    self.warehouse_filter_btn.setObjectName("warehouseFilterOpen")
    self.warehouse_filter_btn.clicked.connect(lambda: _open_warehouse_filter_drawer(self))
    filters.addWidget(self.warehouse_filter_btn)
    layout.addLayout(filters)

    self.warehouse_model = WarehouseInventoryModel(page)
    self.warehouse_view = WarehouseGridView(page)
    self.warehouse_view.setObjectName("warehouseView")
    self.warehouse_view.setViewMode(QListView.IconMode)
    self.warehouse_view.setResizeMode(QListView.Adjust)
    self.warehouse_view.setMovement(QListView.Static)
    self.warehouse_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
    self.warehouse_view.setWrapping(True)
    self.warehouse_view.setUniformItemSizes(True)
    self.warehouse_view.setGridSize(WarehouseCardDelegate.CARD_SIZE)
    self.warehouse_view.setSpacing(0)
    self.warehouse_view.setVerticalScrollMode(QListView.ScrollPerPixel)
    self.warehouse_view.setModel(self.warehouse_model)
    self.warehouse_view.selectionModel().selectionChanged.connect(self._on_warehouse_selection_changed)
    self.warehouse_delegate = WarehouseCardDelegate(self.warehouse_view, text_catalog=getattr(self, 'text_catalog', None))
    self.warehouse_delegate.state_toggle_requested.connect(self._toggle_warehouse_item_state)
    self.warehouse_delegate.identify_requested.connect(self._show_warehouse_item_identification)
    self.warehouse_delegate.compare_requested.connect(lambda index: _select_warehouse_compare_item(self, index, text_catalog=getattr(self, 'text_catalog', None)))
    self.warehouse_view.setItemDelegate(self.warehouse_delegate)
    self.warehouse_view.setStyleSheet(
        themed_style("#warehouseView{background:#0d1117;border:1px solid #21262d;border-radius:10px;padding:8px}")
    )
    layout.addWidget(self.warehouse_view, 1)
    self.warehouse_source_notice = plain_label()
    self.warehouse_source_notice.setWordWrap(True)
    self.warehouse_source_notice.setStyleSheet(themed_style("color:#d29922;padding:4px 8px"))
    self.warehouse_source_notice.hide()
    layout.addWidget(self.warehouse_source_notice)
    self.warehouse_hint = plain_label(_ui_text('warehouse.page.page_warehouse.warehouse_hint'))
    self.warehouse_hint.setWordWrap(True)
    self.warehouse_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
    self.warehouse_hint.setStyleSheet(themed_style("color:#8b949e;padding:8px"))
    layout.addWidget(self.warehouse_hint)
    self._warehouse_all_items = []
    self._warehouse_snapshot_id = None
    self._warehouse_source = None
    self._warehouse_pending_state_changes = {}
    self._warehouse_base_states = {}
    self._warehouse_compare_first = None
    self._warehouse_deferred_snapshot_id = None
    self._warehouse_filter_spec = WarehouseFilterSpec()
    self.warehouse_filter_drawer = WarehouseFilterDrawer(page, catalog=self.text_catalog)
    self.warehouse_filter_drawer.applied.connect(
        lambda spec: _on_warehouse_filter_applied(self, spec)
    )
    return page


def _refresh_warehouse(self):
    """冻结所有读取来源；连续刷新由页面 owner 合并，不丢弃最新请求。"""
    _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
    if not hasattr(self, "warehouse_read_controller"):
        return
    if warehouse_projection_busy(self):
        defer_warehouse_refresh(self, text_catalog=getattr(self, 'text_catalog', None))
        return
    context = self.app_context
    request = WarehouseReadRequest(
        account_id=context.account.active_account_id, generation=context.generation,
        database_path=context.account.user_database_path,
        static_database_path=context.paths.static_database_path,
        asset_root=context.paths.game_ui_asset_root,
        scoring_database_path=context.paths.equipment_allocation_database_path,
        config_dir=context.paths.config_dir,
    )
    self.warehouse_hint.setText(_ui_text('warehouse.page.refresh_warehouse.warehouse_hint'))
    self.warehouse_hint.show()
    self.warehouse_summary.setText(_ui_text('warehouse.page.refresh_warehouse.warehouse_summary'))
    self.warehouse_read_controller.refresh(
        request, apply=lambda result: apply_warehouse_snapshot(self, result, text_catalog=getattr(self, 'text_catalog', None)),
        failed=lambda error: show_warehouse_read_error(self, error, text_catalog=getattr(self, 'text_catalog', None)),
    )


def _apply_warehouse_filters(self, *, timings=None):
    _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
    if not hasattr(self, "warehouse_model"):
        return
    filtered = timed_call(timings, "gui.filter_sort", filter_warehouse_items,
        getattr(self, "_warehouse_all_items", []),
        search=self.warehouse_search.text(),
        spec=getattr(self, "_warehouse_filter_spec", WarehouseFilterSpec()),
    )
    timed_call(timings, "gui.model_reset", self.warehouse_model.set_items, filtered)
    if timings is not None:
        timings.note("gui.model_reset", item_count=len(self._warehouse_all_items), visible_count=len(filtered))
    refresh_conflict_summary(self)
    total = len(getattr(self, "_warehouse_all_items", []))
    self.warehouse_summary.setText(_ui_text('warehouse.page.apply_warehouse_filters.warehouse_summary.state', value=f'{len(filtered)}', total=f'{total}'))
    active_count = getattr(
        self, "_warehouse_filter_spec", WarehouseFilterSpec()
    ).active_group_count
    self.warehouse_filter_btn.setText(
        _ui_text('warehouse.page.apply_warehouse_filters.warehouse_filter_btn.state', active_count=f'{active_count}') if active_count else _ui_text('warehouse.page.apply_warehouse_filters.warehouse_filter_btn.state_2')
    )
    if filtered:
        self.warehouse_hint.hide()
    else:
        self.warehouse_hint.setText(
            _ui_text('warehouse.page.apply_warehouse_filters.warehouse_hint.state') if total == 0 else _ui_text('warehouse.page.apply_warehouse_filters.warehouse_hint.state_2')
        )
        self.warehouse_hint.show()


def _open_warehouse_filter_drawer(self: Any) -> None:
    self._warehouse_filter_spec = self.warehouse_filter_drawer.set_items(self._warehouse_all_items,
        getattr(self, '_warehouse_filter_spec', WarehouseFilterSpec()))
    self.warehouse_filter_drawer.open_for(
        getattr(self, "_warehouse_filter_spec", WarehouseFilterSpec())
    )


def _on_warehouse_filter_applied(self: Any, spec: WarehouseFilterSpec) -> None:
    self._warehouse_filter_spec = spec
    self._apply_warehouse_filters()


def _on_warehouse_sync_state(self, state):
    """Refresh from a later stable snapshot unless the user has local edits."""
    if not hasattr(self, "warehouse_model") or getattr(state, "phase", None) != "listening":
        return
    snapshot_id = getattr(state, "last_snapshot_id", None)
    current_snapshot_id = getattr(self, "_warehouse_snapshot_id", None)
    if not isinstance(snapshot_id, int) or (
        isinstance(current_snapshot_id, int)
        and snapshot_id <= current_snapshot_id
    ):
        return
    deferred_snapshot_id = getattr(
        self,
        "_warehouse_deferred_snapshot_id",
        None,
    )
    self._warehouse_deferred_snapshot_id = max(
        snapshot_id,
        deferred_snapshot_id
        if isinstance(deferred_snapshot_id, int)
        else snapshot_id,
    )
    if warehouse_projection_busy(self):
        defer_warehouse_refresh(self, snapshot_id, text_catalog=getattr(self, 'text_catalog', None))
        return
    self._refresh_warehouse()


def _on_warehouse_selection_changed(self, *_args):
    _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
    if not hasattr(self, "warehouse_view"):
        return
    indexes = self.warehouse_view.selectionModel().selectedIndexes()
    count = len(indexes)
    state_available = bool(indexes) and all(
        isinstance(index.data(Qt.ItemDataRole.UserRole), dict)
        and index.data(Qt.ItemDataRole.UserRole).get("state_known", True)
        for index in indexes
    )
    if hasattr(self, "warehouse_selection_label"):
        self.warehouse_selection_label.setText(_ui_text('warehouse.page.on_warehouse_selection_changed.warehouse_selection_label.state', count=f'{count}'))
    for name in ("warehouse_normal_btn", "warehouse_lock_btn", "warehouse_discard_btn"):
        button = getattr(self, name, None)
        if button is not None:
            button.setEnabled(state_available)


def _clear_manually_resolved_conflicts(host, uids):
    owner = host.warehouse_read_controller
    if owner.conflicts:
        owner.conflicts = [row for row in owner.conflicts if row["uid"] not in uids]
        refresh_conflict_summary(host)


def _set_warehouse_selected_state(
    self: Any,
    target_state: str,
) -> None:
    """Stage the requested state for all selected virtual cards locally."""
    if getattr(self, "_warehouse_management_busy", False):
        return
    if not request_input_entry(self, "native_equipment", "仓库锁定与弃置"):
        return
    if target_state not in {"normal", "locked", "discarded"}:
        return
    indexes = self.warehouse_view.selectionModel().selectedIndexes()
    if not indexes:
        return
    timings, probe = _card_timing(self)
    timings.mark("state_edit")
    changed_uids: set[str] = set()
    pending = dict(getattr(self, "_warehouse_pending_state_changes", {}))
    base_states = dict(getattr(self, "_warehouse_base_states", {}))
    for index in indexes:
        item = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(item, dict) or not item.get("state_known", True):
            continue
        uid = str(item.get("uid") or "")
        original_state = base_states.get(uid)
        if original_state is None:
            continue
        if target_state == original_state:
            pending.pop(uid, None)
        else:
            pending[uid] = target_state
        changed_uids.add(uid)
        self.warehouse_read_controller.recommendation_targets.pop(uid, None)
        self.warehouse_read_controller.manual_targets[uid] = target_state
    if not changed_uids:
        if probe is not None:
            probe.finish("card_unchanged")
        timings.close()
        return
    self._warehouse_pending_state_changes = pending
    self._warehouse_all_items = [
        warehouse_item_with_state(item, target_state, text_catalog=getattr(self, 'text_catalog', None))
        if str(item.get("uid")) in changed_uids
        else item
        for item in self._warehouse_all_items
    ]
    _clear_manually_resolved_conflicts(self, changed_uids)
    timings.since("state_edit", "gui.state_edit")
    self._apply_warehouse_filters(timings=timings)
    self._on_warehouse_selection_changed()
    self._update_warehouse_save_state()
    _finish_card_timing(timings, probe)


def _toggle_warehouse_item_state(
    self: Any,
    index: QModelIndex | None,
    target_state: str,
) -> None:
    """Stage a single card's lock/discard icon action without changing game state yet."""
    if getattr(self, "_warehouse_management_busy", False):
        return
    if not request_input_entry(self, "native_equipment", "仓库锁定与弃置"):
        return
    item = (
        index.data(Qt.ItemDataRole.UserRole)
        if index is not None
        else None
    )
    if (
        not isinstance(item, dict)
        or not item.get("state_known", True)
        or target_state not in {"normal", "locked", "discarded"}
    ):
        return
    uid = str(item.get("uid") or "")
    original_state = getattr(self, "_warehouse_base_states", {}).get(uid)
    if not uid or original_state is None:
        return
    pending = dict(getattr(self, "_warehouse_pending_state_changes", {}))
    if target_state == original_state:
        pending.pop(uid, None)
    else:
        pending[uid] = target_state
    timings, probe = _card_timing(self)
    timings.mark("state_edit")
    self.warehouse_read_controller.recommendation_targets.pop(uid, None)
    self.warehouse_read_controller.manual_targets[uid] = target_state
    self._warehouse_pending_state_changes = pending
    self._warehouse_all_items = [
        warehouse_item_with_state(source, target_state,
                                           text_catalog=getattr(self, 'text_catalog', None)) if str(source.get("uid")) == uid else source
        for source in self._warehouse_all_items
    ]
    _clear_manually_resolved_conflicts(self, {uid})
    timings.since("state_edit", "gui.state_edit")
    self._apply_warehouse_filters(timings=timings)
    self._update_warehouse_save_state()
    _finish_card_timing(timings, probe)


def _save_warehouse_state_changes(self):
    _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
    if not request_input_entry(self, "native_equipment", "保存仓库状态"):
        return
    """Validate manual card edits against the fixed snapshot, then write via nte-core."""
    pending = dict(getattr(self, "_warehouse_pending_state_changes", {}))
    snapshot_id = getattr(self, "_warehouse_snapshot_id", None)
    if not pending:
        QMessageBox.information(self, _ui_text('warehouse.page.save_warehouse_state_changes.qmessagebox'), plain_tooltip(_ui_text('warehouse.page.save_warehouse_state_changes.qmessagebox.body')))
        return
    if not isinstance(snapshot_id, int):
        return
    if getattr(self, "_warehouse_source", "") != "nte_core":
        QMessageBox.information(
            self, _ui_text('warehouse.page.save_warehouse_state_changes.qmessagebox_2'), plain_tooltip(_ui_text('warehouse.page.save_warehouse_state_changes.qmessagebox.body_2'))
        )
        return
    sync_service = getattr(self, "_inventory_sync_service", None)
    if sync_service is None or not sync_service.is_running:
        show_sync_required(self, "保存仓库状态")
        return
    owner = self.warehouse_read_controller
    if owner.closed or owner.state_save_lane.is_running() or getattr(self, "_warehouse_management_busy", False):
        return
    timings = _new_warehouse_timings(self)
    if owner.state_timings is not None:
        owner.state_timings.close()
    owner.state_timings = timings
    context = self.app_context
    account, generation = context.account, context.generation
    cancel = owner.state_save_cancel = Event()
    sync_token = getattr(self, "_inventory_sync_state_token", None)

    def owns_page():
        return (not cancel.is_set() and not owner.closed and context.generation == generation
                and context.account == account)

    def is_current():
        return (owns_page() and self._inventory_sync_service is sync_service
                and getattr(self, "_inventory_sync_state_token", None) is sync_token)

    def accept_delivery():
        if is_current():
            return True
        if owns_page():
            # 同账号重启同步时释放旧操作占用，保留由用户决定是否重试的草稿。
            close_warehouse_state_progress(self)
            self._set_warehouse_management_busy(False)
            timings.finish("save_cancelled", outcome="cancelled")
        return False

    def check_current():
        if not is_current():
            raise CancelledError()

    def failed(error):
        if accept_delivery():
            timings.finish("save_failed", outcome="failed")
            self._on_warehouse_state_error(error)

    service = WarehouseStateManagementService(
        account.user_database_path,
        sync_service,
        input_guard=self.warehouse_read_controller.recommendation_guard,
        operation_context=timings.context, timings=timings, check_current=check_current,
    )
    self._set_warehouse_management_busy(True, self.text_catalog.render('warehouse.management.check_targets'))
    timings.mark("save.plan.queued")
    owner.state_save_lane.submit(
        lambda: _warehouse_timed_worker(timings, "save.plan",
            lambda: service.plan_manual_changes(snapshot_id, pending)),
        lambda plan: self._on_warehouse_manual_plan_ready(plan, service, accept_delivery, failed),
        failed, failed,
    )


def _on_warehouse_manual_plan_ready(self, plan, service, is_current, failed):
    if not is_current():
        return
    owner = self.warehouse_read_controller
    timings = owner.state_timings
    if timings is not None:
        timings.since("save.plan.ready", "plan.delivery_wait")
        timings.flush("plan_received")
    _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
    if not plan.changes:
        if timings is not None:
            timings.finish("plan_empty")
        self._warehouse_pending_state_changes = {}
        self._set_warehouse_management_busy(False)
        self._update_warehouse_save_state()
        QMessageBox.information(self, _ui_text('warehouse.page.on_warehouse_manual_plan_ready.qmessagebox'), plain_tooltip(self.text_catalog.render('warehouse.management.already_current')))
        return
    counts = _warehouse_target_counts(plan.changes)
    if timings is not None:
        timings.mark("user_confirmation")
    message = (_ui_text('warehouse.management.confirm_save_without_counts') if counts is None else
               _ui_text('warehouse.management.confirm_save', count=len(plan.changes), **counts))
    answer = QMessageBox.question(
        self, _ui_text('warehouse.page.on_warehouse_manual_plan_ready.qmessagebox_2'),
        message, QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
    )
    if not is_current():
        return
    if answer != QMessageBox.Yes:
        self._set_warehouse_management_busy(False)
        if timings is not None:
            timings.since("user_confirmation", "gui.user_confirmation")
            timings.finish("save_cancelled", outcome="cancelled")
        return
    if timings is not None:
        timings.since("user_confirmation", "gui.user_confirmation")
        timings.mark("save.apply.queued")
    self._set_warehouse_management_busy(True, "正在保存弃置/锁定状态到游戏…")
    progress_callback = show_warehouse_state_progress(
        self,
        change_count=len(plan.changes),
    )
    owner.state_save_lane.submit(
        lambda: _warehouse_timed_worker(timings, "save.apply", lambda: service.apply(
            plan, progress_callback=progress_callback)),
        lambda result: self._on_warehouse_state_applied(result, is_current=is_current),
        failed, failed,
    )


def _open_warehouse_state_manager(self):
    """仓库独立编辑器只生成草稿，游戏写入始终由仓库右上角保存入口拥有。"""
    if not request_input_entry(self, "native_equipment", "仓库状态管理"):
        return
    if self.warehouse_read_controller.state_save_lane.is_running() or getattr(self, "_warehouse_management_busy", False):
        return
    if getattr(self, "_warehouse_source", "") != "nte_core":
        QMessageBox.information(self, self.text_catalog.render('warehouse.management.title'),
            plain_tooltip(self.text_catalog.render('warehouse.management.source_required')))
        return
    self.warehouse_management_controller.open()


def _warehouse_target_counts(changes):
    if not isinstance(changes, (list, tuple)):
        return None
    keys = {"discarded": "discard", "locked": "lock", "normal": "normal"}
    counts = dict.fromkeys(keys.values(), 0)
    for change in changes:
        target = change.get("target_state") if isinstance(change, Mapping) else None
        if not isinstance(target, str) or target not in keys:
            return None
        counts[keys[target]] += 1
    return counts


def _on_warehouse_state_applied(self, result, *, is_current=lambda: True):
    if not is_current():
        return
    timings = self.warehouse_read_controller.state_timings
    if timings is not None:
        timings.since("save.apply.ready", "save.delivery_wait")
        timings.flush("save_received")
    _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
    close_warehouse_state_progress(self)
    # 结果弹窗会运行嵌套事件循环；完成后验判断前仍由状态操作拥有面板。
    self._set_warehouse_management_busy(True)
    after_snapshot_id = getattr(result, "after_snapshot_id", None)
    has_new_snapshot = (
        isinstance(after_snapshot_id, int)
        and after_snapshot_id > result.before_snapshot_id
    )
    applied_states = {
        str(change.get("uid") or ""): str(change.get("target_state") or "")
        for change in getattr(result, "changes", ())
        if str(change.get("uid") or "") and str(change.get("target_state") or "") in {"normal", "locked", "discarded"}
    }
    # 缺新稳定快照时只保留标注为待确认的 RPC 接受投影，后续同步证据才是事实。
    if applied_states and not has_new_snapshot:
        self._warehouse_all_items = [
            warehouse_item_with_state(
                item,
                applied_states.get(
                    str(item.get("uid") or ""),
                    "discarded" if item.get("discarded") else "locked" if item.get("locked") else "normal",
                ),
                text_catalog=getattr(self, 'text_catalog', None),
            )
            if str(item.get("uid") or "") in applied_states
            else item
            for item in getattr(self, "_warehouse_all_items", [])
        ]
    counts = _warehouse_target_counts(getattr(result, "reported_changes", None))
    result_message = ""
    if counts is not None:
        key = 'warehouse.management.result.confirmed_counts' if result.verified else 'warehouse.management.result.submitted_counts'
        result_message = _ui_text(key, **counts)
    if getattr(result, "inventory_reduction_observed", False):
        result_message += (
            "\n\n检测到库存减少；如游戏内未分解库存，请在工作台点击“重启同步”，等待完整背包读取完成。"
        )
    if getattr(result, "verified", False) and not getattr(result, "inventory_reduction_observed", False):
        result_message += (
            localized_text(getattr(self, 'text_catalog', None), 'auto.features.inventory.warehouse.controller.on_warehouse_state_applied.f55ac201c', '\n\n已通过游戏返回的新稳定快照 #{after_snapshot_id} 确认，仓库将自动刷新。', after_snapshot_id=f'{after_snapshot_id}')
        )
        timed_call(timings, "gui.result_prompt", QMessageBox.information,
            self,
            _ui_text('warehouse.page.on_warehouse_state_applied.qmessagebox'),
            result_message.lstrip(),
        )
    else:
        if not getattr(result, "verified", False):
            result_message += (
                localized_text(getattr(self, 'text_catalog', None), 'auto.features.inventory.warehouse.controller.on_warehouse_state_applied.f1700c2ce', '\n\n修改指令已经提交，但尚未从游戏快照完整确认。\n{value}', value=f"{getattr(result, 'verification_error', None) or ui_enum_text(getattr(self, 'text_catalog', None), '等待后续背包快照确认。')}")
            )
        timed_call(timings, "gui.result_prompt", QMessageBox.warning,
            self,
            _ui_text('warehouse.page.on_warehouse_state_applied.qmessagebox.state') if getattr(result, "inventory_reduction_observed", False) else _ui_text('warehouse.page.on_warehouse_state_applied.qmessagebox.state_2'),
            result_message.lstrip(),
        )
    if not is_current():
        return
    self._warehouse_pending_state_changes = {}
    self.warehouse_read_controller.manual_targets = {}
    self.warehouse_read_controller.recommendation_targets = {}
    self.warehouse_read_controller.recommendation_guard = None
    self.warehouse_read_controller.conflicts = []
    self.warehouse_read_controller.conflict_guard = None
    self.warehouse_read_controller.management_issues = []
    refresh_conflict_summary(self)
    self._on_warehouse_selection_changed()
    self._update_warehouse_save_state()
    deferred_snapshot_id = getattr(
        self,
        "_warehouse_deferred_snapshot_id",
        None,
    )
    should_refresh = has_new_snapshot or (
        isinstance(deferred_snapshot_id, int)
        and deferred_snapshot_id > result.before_snapshot_id
    )
    self._warehouse_confirmation_after = result.before_snapshot_id if applied_states and not has_new_snapshot else None
    if should_refresh:
        self._warehouse_refresh_deferred = False
        self._set_warehouse_management_busy(False)
        self._refresh_warehouse()
        if timings is not None:
            timings.finish("save_refresh_queued")
        return
    self._warehouse_base_states = {
        str(item.get("uid")): (
            "discarded"
            if item.get("discarded")
            else "locked"
            if item.get("locked")
            else "normal"
        )
        for item in getattr(self, "_warehouse_all_items", [])
    }
    self._apply_warehouse_filters(timings=timings)
    self.warehouse_hint.setText(
        _ui_text('warehouse.page.on_warehouse_state_applied.warehouse_hint')
    )
    self.warehouse_hint.show()
    self._set_warehouse_management_busy(False)
    if timings is not None:
        timings.finish("save_projected")


class WarehouseControllerMixin:
    _page_warehouse = _page_warehouse
    _refresh_warehouse = _refresh_warehouse
    _apply_warehouse_filters = _apply_warehouse_filters
    _on_warehouse_sync_state = _on_warehouse_sync_state
    _on_warehouse_selection_changed = _on_warehouse_selection_changed
    _set_warehouse_selected_state = _set_warehouse_selected_state
    _toggle_warehouse_item_state = _toggle_warehouse_item_state
    _save_warehouse_state_changes = _save_warehouse_state_changes
    _show_warehouse_item_identification = _show_warehouse_item_identification
    _update_warehouse_save_state = _update_warehouse_save_state
    _on_warehouse_manual_plan_ready = _on_warehouse_manual_plan_ready
    _open_warehouse_state_manager = _open_warehouse_state_manager
    _on_warehouse_state_applied = _on_warehouse_state_applied
    _on_warehouse_state_error = _on_warehouse_state_error
    _set_warehouse_management_busy = _set_warehouse_management_busy
