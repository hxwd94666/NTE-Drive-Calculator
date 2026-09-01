# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.i18n import tr
from src.app.theme import themed_style
from src.app.workers import WorkerThread
from src.domain.warehouse_filter import WarehouseFilterSpec
from src.services.inventory_source_capabilities import is_visual_inventory_source
from src.features.inventory.warehouse import (
    WarehouseCardDelegate,
    WarehouseGridView,
    WarehouseInventoryModel,
    filter_warehouse_items,
    warehouse_item_with_state,
)
from src.features.inventory.warehouse_filter_drawer import WarehouseFilterDrawer
from src.features.inventory.warehouse_presenter import load_warehouse_snapshot
from src.features.inventory.warehouse_progress import (
    close_warehouse_state_progress,
    on_warehouse_state_error as _on_warehouse_state_error,
    set_warehouse_management_busy as _set_warehouse_management_busy,
    show_warehouse_state_progress,
    update_warehouse_save_state as _update_warehouse_save_state,
)
from src.features.inventory.warehouse_identification_controller import (
    select_warehouse_compare_item as _select_warehouse_compare_item,
    show_warehouse_item_identification as _show_warehouse_item_identification,
)
from src.features.scanning.post_action_dialog import (
    load_scan_post_action_config,
    show_scan_post_action_dialog,
)
from src.domain.post_actions import validate_post_action_config
from src.observability import OperationContext
from src.services.warehouse_state_management import WarehouseStateManagementService
from src.utils.logger import logger


__all__ = [
    "_equipment_compare_signature",
    "_same_equipment_by_ocr",
    "_page_equipment",
    "_refresh_equip",
    "_page_warehouse",
    "_refresh_warehouse",
    "_apply_warehouse_filters",
    "_on_warehouse_sync_state",
    "_on_warehouse_selection_changed",
    "_set_warehouse_selected_state",
    "_toggle_warehouse_item_state",
    "_save_warehouse_state_changes",
    "_show_warehouse_item_identification",
    "_update_warehouse_save_state",
    "_on_warehouse_manual_plan_ready",
    "_open_warehouse_state_manager",
    "_on_warehouse_state_plan_ready",
    "_on_warehouse_state_applied",
    "_on_warehouse_state_error",
    "_set_warehouse_management_busy",
    "_saved_plan_diff_text",
    "_show_saved_plan_diff_dialog",
    "_clear_all_equipment",
    "_delete_role_equipment",
    "_optimize_saved_equipment",
    "_preview_assemble_role",
    "_preview_fast_assemble_all_roles",
    "_preview_automatic_assemble_all_roles",
]

EQUIPMENT_ROLE_PLACEHOLDER_HEIGHT = 520
EQUIPMENT_VIEWPORT_PREFETCH_COUNT = 1
# Legacy test hosts and non-Qt callers retain the old batch-only path.
EQUIPMENT_INITIAL_RENDER_COUNT = 8
EQUIPMENT_RENDER_BATCH_SIZE = 3

_OFFICIAL_STAT_LABELS = {
    "AtkAdd": "攻击力",
    "AtkUp": "攻击力%",
    "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%",
    "DamageUpChaosBase": "暗属性异能伤害增强%",
    "DamageUpCosmosBase": "光属性异能伤害增强%",
    "DamageUpGeneralBase": "伤害增加%",
    "DamageUpIncantationBase": "咒属性异能伤害增强%",
    "DamageUpLakshanaBase": "相属性异能伤害增强%",
    "DamageUpNatureBase": "灵属性异能伤害增强%",
    "DamageUpPsycheBase": "魂属性异能伤害增强%",
    "DamageUpPsychicallyBase": "心灵伤害增强%",
    "DefAdd": "防御力",
    "DefUp": "防御力%",
    "HealUp": "治疗加成",
    "HPMaxAdd": "生命值",
    "HPMaxUp": "生命值%",
    "MagBase": "环合强度",
    "UnbalIntensityBase": "倾陷强度",
}
_OFFICIAL_SHAPE_LABELS = {
    "hen2": "H_2",
    "hen3": "H_3",
    "hen4": "H_4",
    "shu2": "V_2",
    "shu3": "V_3",
    "shu4": "V_4",
    "z3": "Trap_4_H",
    "z4": "Trap_4_V",
    "zhijiao1": "L_3_BL",
    "zhijiao2": "L_3_TL",
    "zhijiao3": "L_3_TR",
    "zhijiao4": "L_3_BR",
}


def _page_warehouse(self):
    """Create the virtualized official-inventory page without loading items yet."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)

    title_row = QHBoxLayout()
    title = QLabel(tr("仓库"))
    title.setStyleSheet(themed_style("font-size:18px;font-weight:700;color:#f0f6fc"))
    title_row.addWidget(title)
    self.warehouse_summary = QLabel(tr("读取背包稳定快照中…"))
    self.warehouse_summary.setStyleSheet(themed_style("color:#8b949e;margin-left:8px"))
    title_row.addWidget(self.warehouse_summary)
    self.warehouse_selection_label = QLabel(tr("选中 0 件"))
    self.warehouse_selection_label.setStyleSheet(themed_style("color:#8b949e;margin-left:8px"))
    title_row.addWidget(self.warehouse_selection_label)
    multi_select_hint = QLabel(tr("（按住 CTRL 多选）"))
    multi_select_hint.setStyleSheet(themed_style("color:#8b949e"))
    title_row.addWidget(multi_select_hint)
    self.warehouse_normal_btn = QPushButton(tr("正常"))
    self.warehouse_lock_btn = QPushButton(tr("锁定"))
    self.warehouse_discard_btn = QPushButton(tr("弃置"))
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
    self.warehouse_manage_btn = QPushButton(tr("管理"))
    self.warehouse_manage_btn.setObjectName("btnPrimary")
    self.warehouse_manage_btn.setToolTip(tr("按管理规则一键同步弃置/锁定状态"))
    self.warehouse_manage_btn.clicked.connect(self._open_warehouse_state_manager)
    title_row.addWidget(self.warehouse_manage_btn)
    self.warehouse_save_btn = QPushButton(tr("保存"))
    self.warehouse_save_btn.setObjectName("btnPrimary")
    self.warehouse_save_btn.setStyleSheet(
        themed_style(
            "QPushButton{background:#1f6feb;border-color:#388bfd;color:white;}"
            "QPushButton:hover{background:#388bfd;}"
            "QPushButton:disabled{background:#30363d;color:#8b949e;}"
        )
    )
    self.warehouse_save_btn.setToolTip(tr("将手动修改的弃置/锁定状态写入游戏"))
    self.warehouse_save_btn.setEnabled(True)
    self.warehouse_save_btn.clicked.connect(self._save_warehouse_state_changes)
    title_row.addWidget(self.warehouse_save_btn)
    layout.addLayout(title_row)

    filters = QHBoxLayout()
    filters.setSpacing(8)
    self.warehouse_search = QLineEdit()
    self.warehouse_search.setPlaceholderText(tr("搜索装备、套装、词条或已装备角色名…"))
    self.warehouse_search.setClearButtonEnabled(True)
    self.warehouse_search.setMinimumWidth(280)
    self.warehouse_search.textChanged.connect(self._apply_warehouse_filters)
    filters.addWidget(self.warehouse_search, 1)
    self.warehouse_filter_btn = QPushButton(tr("筛选"))
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
    self.warehouse_delegate = WarehouseCardDelegate(self.warehouse_view)
    self.warehouse_delegate.state_toggle_requested.connect(self._toggle_warehouse_item_state)
    self.warehouse_delegate.identify_requested.connect(self._show_warehouse_item_identification)
    self.warehouse_delegate.compare_requested.connect(lambda index: _select_warehouse_compare_item(self, index))
    self.warehouse_view.setItemDelegate(self.warehouse_delegate)
    self.warehouse_view.setStyleSheet(
        themed_style("#warehouseView{background:#0d1117;border:1px solid #21262d;border-radius:10px;padding:8px}")
    )
    layout.addWidget(self.warehouse_view, 1)
    self.warehouse_hint = QLabel(tr("仓库将在打开此页面时读取最新稳定背包快照。"))
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
    self.warehouse_filter_drawer = WarehouseFilterDrawer(page)
    self.warehouse_filter_drawer.applied.connect(
        lambda spec: _on_warehouse_filter_applied(self, spec)
    )
    return page


def _refresh_warehouse(self):
    """Load a fixed snapshot on a worker; never query SQLite on the UI thread."""
    existing = getattr(self, "_warehouse_load_worker", None)
    if existing is not None and existing.isRunning():
        return
    if not hasattr(self, "warehouse_model"):
        return
    token = object()
    self._warehouse_load_token = token
    self.warehouse_hint.setText(tr("正在读取背包稳定快照…"))
    self.warehouse_hint.show()
    self.warehouse_summary.setText(tr("读取中…"))
    database_path = self.app_context.account.user_database_path
    operation = OperationContext.create(
        "warehouse",
        account_id=self.app_context.account.active_account_id,
        context_generation=self.app_context.generation,
    )
    self._warehouse_load_operation = operation
    worker = WorkerThread(
        target=lambda: load_warehouse_snapshot(database_path, operation),
        parent=self,
    )
    self._warehouse_load_worker = worker
    worker.result_ready.connect(lambda result, current=token: _on_warehouse_loaded(self, current, result))
    worker.error.connect(lambda error, current=token: _on_warehouse_load_error(self, current, error))
    worker.start()


def _on_warehouse_loaded(self, token, result):
    if token is not getattr(self, "_warehouse_load_token", None):
        return
    self._warehouse_snapshot_id = result.get("snapshot_id")
    deferred_snapshot_id = getattr(
        self,
        "_warehouse_deferred_snapshot_id",
        None,
    )
    if (
        isinstance(deferred_snapshot_id, int)
        and isinstance(self._warehouse_snapshot_id, int)
        and self._warehouse_snapshot_id >= deferred_snapshot_id
    ):
        self._warehouse_deferred_snapshot_id = None
    self._warehouse_source = str(result.get("source") or "")
    self._warehouse_all_items = list(result.get("items") or [])
    self._warehouse_pending_state_changes = {}
    self._warehouse_base_states = {
        str(item.get("uid")): "discarded" if item.get("discarded") else "locked" if item.get("locked") else "normal"
        for item in self._warehouse_all_items
        if item.get("state_known", True)
    }
    self._warehouse_compare_first = None
    self._warehouse_filter_spec = self.warehouse_filter_drawer.set_items(
        self._warehouse_all_items,
        getattr(self, "_warehouse_filter_spec", WarehouseFilterSpec()),
    )
    self._apply_warehouse_filters()
    if is_visual_inventory_source(self._warehouse_source):
        self.warehouse_hint.setText(tr("当前为全量扫描库存：等级、锁定/弃置状态和已装备角色无法识别；鉴定与对比仍可使用。"))
        self.warehouse_hint.show()


def _on_warehouse_load_error(self, token, error):
    if token is not getattr(self, "_warehouse_load_token", None):
        return
    self._warehouse_all_items = []
    self.warehouse_model.set_items([])
    self.warehouse_summary.setText(tr("读取失败"))
    self.warehouse_hint.setText(tr("仓库读取失败：{error}", error=error))
    self.warehouse_hint.show()
    logger.error(f"读取仓库稳定快照失败: {error}")


def _apply_warehouse_filters(self):
    if not hasattr(self, "warehouse_model"):
        return
    filtered = filter_warehouse_items(
        getattr(self, "_warehouse_all_items", []),
        search=self.warehouse_search.text(),
        spec=getattr(self, "_warehouse_filter_spec", WarehouseFilterSpec()),
    )
    self.warehouse_model.set_items(filtered)
    total = len(getattr(self, "_warehouse_all_items", []))
    self.warehouse_summary.setText(
        tr("显示 {shown} / {total} 件", shown=len(filtered), total=total)
    )
    active_count = getattr(
        self, "_warehouse_filter_spec", WarehouseFilterSpec()
    ).active_group_count
    self.warehouse_filter_btn.setText(
        tr("筛选 ({count})", count=active_count) if active_count else tr("筛选")
    )
    if filtered:
        self.warehouse_hint.hide()
    else:
        self.warehouse_hint.setText(tr("当前筛选条件下没有装备。请先完成背包同步，或调整筛选条件。"))
        self.warehouse_hint.show()


def _open_warehouse_filter_drawer(self: Any) -> None:
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
    active_worker = getattr(self, "_warehouse_state_worker", None)
    state_change_running = (
        active_worker is not None
        and active_worker.isRunning()
    )
    if (
        getattr(self, "_warehouse_pending_state_changes", {})
        or state_change_running
    ):
        self.warehouse_hint.setText(
            tr("游戏背包已有新快照；当前修改完成后仓库将自动刷新。")
        )
        self.warehouse_hint.show()
        return
    self._refresh_warehouse()


def _on_warehouse_selection_changed(self, *_args):
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
        self.warehouse_selection_label.setText(tr("选中 {count} 件", count=count))
    for name in ("warehouse_normal_btn", "warehouse_lock_btn", "warehouse_discard_btn"):
        button = getattr(self, name, None)
        if button is not None:
            button.setEnabled(state_available)


def _set_warehouse_selected_state(
    self: Any,
    target_state: str,
) -> None:
    """Stage the requested state for all selected virtual cards locally."""
    if target_state not in {"normal", "locked", "discarded"}:
        return
    indexes = self.warehouse_view.selectionModel().selectedIndexes()
    if not indexes:
        return
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
    if not changed_uids:
        return
    self._warehouse_pending_state_changes = pending
    self._warehouse_all_items = [
        warehouse_item_with_state(
            item,
            pending.get(
                str(item.get("uid")),
                "discarded" if item.get("discarded") else "locked" if item.get("locked") else "normal",
            ),
        )
        if str(item.get("uid")) in changed_uids
        else item
        for item in self._warehouse_all_items
    ]
    self._apply_warehouse_filters()
    self._on_warehouse_selection_changed()
    self._update_warehouse_save_state()


def _toggle_warehouse_item_state(
    self: Any,
    index: QModelIndex | None,
    target_state: str,
) -> None:
    """Stage a single card's lock/discard icon action without changing game state yet."""
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
    self._warehouse_pending_state_changes = pending
    self._warehouse_all_items = [
        warehouse_item_with_state(source, target_state) if str(source.get("uid")) == uid else source
        for source in self._warehouse_all_items
    ]
    self._apply_warehouse_filters()
    self._update_warehouse_save_state()


def _save_warehouse_state_changes(self):
    """Validate manual card edits against the fixed snapshot, then write via nte-core."""
    pending = dict(getattr(self, "_warehouse_pending_state_changes", {}))
    snapshot_id = getattr(self, "_warehouse_snapshot_id", None)
    if not pending:
        QMessageBox.information(self, tr("仓库保存"), tr("没有待保存的弃置/锁定状态修改。"))
        return
    if not isinstance(snapshot_id, int):
        return
    if getattr(self, "_warehouse_source", "") != "nte_core":
        QMessageBox.information(
            self, tr("仓库状态不可用"), tr("全量扫描库存无法读取或修改锁定、弃置状态；请先获取背包同步快照。")
        )
        return
    active_worker = getattr(self, "_warehouse_state_worker", None)
    if active_worker is not None and active_worker.isRunning():
        return
    sync_service = getattr(self, "_inventory_sync_service", None)
    if sync_service is None or not sync_service.is_running:
        QMessageBox.warning(self, tr("无法保存仓库状态"), tr("请先在工作台启动背包同步，并等待状态显示为稳定监听。"))
        return
    service = WarehouseStateManagementService(
        self.app_context.account.user_database_path,
        sync_service,
        operation_context=OperationContext.create(
            "warehouse",
            account_id=self.app_context.account.active_account_id,
            context_generation=self.app_context.generation,
            snapshot_id=snapshot_id,
        ),
    )
    self._warehouse_state_service = service
    self._set_warehouse_management_busy(True, tr("正在检查手动修改…"))
    worker = WorkerThread(
        target=lambda: service.plan_manual_changes(snapshot_id, pending),
        parent=self,
    )
    self._warehouse_state_worker = worker
    worker.result_ready.connect(self._on_warehouse_manual_plan_ready)
    worker.error.connect(self._on_warehouse_state_error)
    worker.start()


def _on_warehouse_manual_plan_ready(self, plan):
    self._set_warehouse_management_busy(False)
    if not plan.changes:
        self._warehouse_pending_state_changes = {}
        self._update_warehouse_save_state()
        QMessageBox.information(self, tr("仓库保存"), tr("所有手动状态已与当前游戏背包一致。"))
        return
    counts = {"弃置": 0, "锁定": 0, "正常": 0}
    for change in plan.changes:
        counts[{"discarded": "弃置", "locked": "锁定", "normal": "正常"}[change["target_state"]]] += 1
    message = tr(
        "将保存 {total} 件装备的手动状态：弃置 {discard} 件，"
        "锁定 {lock} 件，恢复正常 {normal} 件。\n\n"
        "确认后会通过本地核心组件直接写入游戏。",
        total=len(plan.changes),
        discard=counts["弃置"],
        lock=counts["锁定"],
        normal=counts["正常"],
    )
    if (
        QMessageBox.question(
            self, tr("确认保存仓库状态"), message, QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel
        )
        != QMessageBox.Yes
    ):
        return
    service = self._warehouse_state_service
    self._set_warehouse_management_busy(True, tr("正在保存弃置/锁定状态到游戏…"))
    progress_callback = show_warehouse_state_progress(
        self,
        change_count=len(plan.changes),
    )
    worker = WorkerThread(
        target=lambda: service.apply(
            plan,
            progress_callback=progress_callback,
        ),
        parent=self,
    )
    self._warehouse_state_worker = worker
    worker.result_ready.connect(self._on_warehouse_state_applied)
    worker.error.connect(self._on_warehouse_state_error)
    worker.start()


def _open_warehouse_state_manager(self):
    """Open the existing rule editor, then apply its result through nte-core."""
    active_worker = getattr(self, "_warehouse_state_worker", None)
    if active_worker is not None and active_worker.isRunning():
        return
    if getattr(self, "_warehouse_source", "") != "nte_core":
        QMessageBox.information(
            self, tr("仓库管理不可用"), tr("全量扫描库存无法读取或修改锁定、弃置状态；请先获取背包同步快照。")
        )
        return
    account = self.app_context.account
    if not show_scan_post_action_dialog(
        self,
        account.user_config_dir,
        self.app_context.paths.config_dir,
        user_database_path=account.user_database_path,
        window_title=tr("仓库弃置/锁定管理"),
    ):
        return
    config = load_scan_post_action_config(
        account.user_config_dir,
        user_database_path=account.user_database_path,
    )
    error = validate_post_action_config(config)
    if error:
        QMessageBox.warning(self, tr("管理配置无效"), error)
        return
    sync_service = getattr(self, "_inventory_sync_service", None)
    if sync_service is None or not sync_service.is_running:
        QMessageBox.warning(self, tr("无法管理仓库"), tr("请先在工作台启动背包同步，并等待状态显示为稳定监听。"))
        return
    service = WarehouseStateManagementService(
        account.user_database_path,
        sync_service,
        config_dir=self.app_context.paths.config_dir,
        operation_context=OperationContext.create(
            "warehouse",
            account_id=account.active_account_id,
            context_generation=self.app_context.generation,
            snapshot_id=getattr(self, "_warehouse_snapshot_id", None),
        ),
    )
    self._warehouse_state_service = service
    self._set_warehouse_management_busy(True, tr("正在计算弃置/锁定目标…"))
    worker = WorkerThread(target=lambda: service.evaluate(config), parent=self)
    self._warehouse_state_worker = worker
    worker.result_ready.connect(self._on_warehouse_state_plan_ready)
    worker.error.connect(self._on_warehouse_state_error)
    worker.start()


def _on_warehouse_state_plan_ready(self, plan):
    self._set_warehouse_management_busy(False)
    if not plan.changes:
        QMessageBox.information(self, tr("仓库管理"), tr("当前稳定背包没有符合规则、需要变更状态的装备。"))
        return
    counts = {"弃置": 0, "锁定": 0, "取消弃置/锁定": 0}
    for change in plan.changes:
        target = change.get("target_state")
        if target == "discarded":
            counts["弃置"] += 1
        elif target == "locked":
            counts["锁定"] += 1
        else:
            counts["取消弃置/锁定"] += 1
    message = tr(
        "将按快照 #{snapshot} 操作 {total} 件装备：\n"
        "弃置 {discard} 件，锁定 {lock} 件，"
        "取消状态 {clear} 件。\n\n"
        "确认后会通过本地核心组件直接写入游戏。",
        snapshot=plan.snapshot_id,
        total=len(plan.changes),
        discard=counts["弃置"],
        lock=counts["锁定"],
        clear=counts["取消弃置/锁定"],
    )
    if (
        QMessageBox.question(self, tr("确认一键管理"), message, QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        != QMessageBox.Yes
    ):
        return
    service = self._warehouse_state_service
    self._set_warehouse_management_busy(True, tr("正在通过本地核心组件同步弃置/锁定状态…"))
    progress_callback = show_warehouse_state_progress(
        self,
        change_count=len(plan.changes),
    )
    worker = WorkerThread(
        target=lambda: service.apply(
            plan,
            progress_callback=progress_callback,
        ),
        parent=self,
    )
    self._warehouse_state_worker = worker
    worker.result_ready.connect(self._on_warehouse_state_applied)
    worker.error.connect(self._on_warehouse_state_error)
    worker.start()


def _on_warehouse_state_applied(self, result):
    close_warehouse_state_progress(self)
    self._set_warehouse_management_busy(False)
    summary = result.summary
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
    # Without a new stable snapshot, retain the accepted RPC projection as a
    # clearly-labelled fallback.  A later sync event remains authoritative.
    if applied_states and not has_new_snapshot:
        self._warehouse_all_items = [
            warehouse_item_with_state(
                item,
                applied_states.get(
                    str(item.get("uid") or ""),
                    "discarded" if item.get("discarded") else "locked" if item.get("locked") else "normal",
                ),
            )
            if str(item.get("uid") or "") in applied_states
            else item
            for item in getattr(self, "_warehouse_all_items", [])
        ]
    result_message = tr(
        "已完成弃置/锁定操作：弃置 {discard} 件，锁定 {lock} 件，"
        "取消弃置 {discard_clear} 件，取消锁定 {lock_clear} 件。",
        discard=summary["discard_set_count"], lock=summary["lock_set_count"],
        discard_clear=summary["discard_clear_count"], lock_clear=summary["lock_clear_count"],
    )
    if getattr(result, "inventory_reduction_observed", False):
        result_message += tr(
            "\n\n检测到库存减少；如游戏内未分解库存，请重新在游戏登录页面背包同步。"
        )
    if getattr(result, "verified", False) and not getattr(result, "inventory_reduction_observed", False):
        result_message += tr(
            "\n\n已通过游戏返回的新稳定快照 #{snapshot} 确认，仓库将自动刷新。",
            snapshot=after_snapshot_id,
        )
        QMessageBox.information(
            self,
            tr("仓库状态已确认"),
            result_message,
        )
    else:
        if not getattr(result, "verified", False):
            result_message += tr(
                "\n\n修改指令已经提交，但尚未从游戏快照完整确认。\n{detail}",
                detail=getattr(result, "verification_error", None) or tr("等待后续背包快照确认。"),
            )
        QMessageBox.warning(
            self,
            tr("检测到库存减少")
            if getattr(result, "inventory_reduction_observed", False)
            else tr("仓库状态待确认"),
            result_message,
        )
    self._warehouse_pending_state_changes = {}
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
    if should_refresh:
        self._refresh_warehouse()
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
    self._apply_warehouse_filters()
    self.warehouse_hint.setText(
        tr("修改已提交，当前页面暂按核心组件接受结果显示；"
        "收到后续稳定快照时将自动刷新。")
    )
    self.warehouse_hint.show()


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
    _on_warehouse_state_plan_ready = _on_warehouse_state_plan_ready
    _on_warehouse_state_applied = _on_warehouse_state_applied
    _on_warehouse_state_error = _on_warehouse_state_error
    _set_warehouse_management_busy = _set_warehouse_management_busy
