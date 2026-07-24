# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QInputDialog, QLineEdit, QListView, QMessageBox, QProgressDialog, QPushButton, QScrollArea, \
    QVBoxLayout, QWidget

from src.app import runtime
from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.app.theme import GRADE_COLORS, current_style_sheet, theme_color, theme_rgba, themed_style
from src.app.workers import WorkerThread
from src.features.drive_assembly.ui_bridge import (
    execute_all_roles_from_current_game_page,
    execute_selected_role_from_current_game_page,
)
from src.features.role.replacement_service import (
    build_equipment_role_context,
    rank_replacement_candidates_by_damage,
)
from src.features.scanning.file_lifecycle import equipment_compare_signature
from src.features.inventory.warehouse import (
    WarehouseCardDelegate,
    WarehouseGridView,
    WarehouseInventoryModel,
    filter_warehouse_items,
    load_warehouse_snapshot,
    warehouse_item_compare_category,
    warehouse_item_with_state,
    warehouse_item_view,
    warehouse_type_options,
)
from src.features.identification.page import build_identify_result_row
from src.features.scanning.post_action_dialog import (
    load_scan_post_action_config,
    show_scan_post_action_dialog,
)
from src.features.scanning.post_actions import validate_post_action_config
from src.services.equipment_apply_service import EquipmentApplyService
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.warehouse_identification_service import WarehouseIdentificationService
from src.services.warehouse_state_management import WarehouseStateManagementService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    virtual_equipment_inventory_item,
)
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_DISPLAY_NAME,
    EQUIP_GRADE,
    EQUIP_IS_CHANGED,
    EQUIP_IS_NEW,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SCORE,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_UID,
    ROLE_BLUEPRINT_LAYOUT,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    ROLE_LAST_DIFF,
    ROLE_TOTAL_GRADE,
    ROLE_TOTAL_SCORE,
)
from src.ui.puzzle_board import PuzzleBoardWidget
from src.ui.widgets import match_pinyin as _match_pinyin
from src.utils.logger import logger
from src.ui.main_window_method_install import install_methods as _install_main_window_methods


def set_bonus_from_tape_source(source) -> dict:
    """Build a safe placeholder while tape-set bonus details move to SQLite."""
    if isinstance(source, dict):
        set_name = str(source.get("set_name", "") or "")
    else:
        set_name = str(getattr(source, "set_name", "") or "")
    return {"display_name": set_name, "skill": {}, "skill_2": {}, "skill_cover": 0.8}

__all__ = ['_equipment_compare_signature', '_same_equipment_by_ocr', '_page_equipment', '_refresh_equip',
           '_page_warehouse', '_refresh_warehouse', '_apply_warehouse_filters', '_on_warehouse_sync_state',
           '_on_warehouse_selection_changed', '_set_warehouse_selected_state', '_toggle_warehouse_item_state', '_save_warehouse_state_changes',
           '_show_warehouse_item_identification', '_update_warehouse_save_state', '_on_warehouse_manual_plan_ready', '_open_warehouse_state_manager',
           '_on_warehouse_state_plan_ready', '_on_warehouse_state_applied', '_on_warehouse_state_error',
           '_set_warehouse_management_busy',
           '_saved_plan_diff_text', '_show_saved_plan_diff_dialog', '_clear_all_equipment', '_delete_role_equipment', '_optimize_saved_equipment',
           '_preview_assemble_role', '_preview_fast_assemble_all_roles', '_preview_automatic_assemble_all_roles']

EQUIPMENT_ROLE_PLACEHOLDER_HEIGHT = 520
EQUIPMENT_VIEWPORT_PREFETCH_COUNT = 1
# Legacy test hosts and non-Qt callers retain the old batch-only path.
EQUIPMENT_INITIAL_RENDER_COUNT = 8
EQUIPMENT_RENDER_BATCH_SIZE = 3

_OFFICIAL_STAT_LABELS = {
    "AtkAdd": "攻击力", "AtkUp": "攻击力%", "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%", "DamageUpChaosBase": "暗属性异能伤害增强%",
    "DamageUpCosmosBase": "光属性异能伤害增强%", "DamageUpGeneralBase": "伤害增加%",
    "DamageUpIncantationBase": "咒属性异能伤害增强%", "DamageUpLakshanaBase": "相属性异能伤害增强%",
    "DamageUpNatureBase": "灵属性异能伤害增强%", "DamageUpPsycheBase": "魂属性异能伤害增强%",
    "DamageUpPsychicallyBase": "心灵伤害增强%", "DefAdd": "防御力", "DefUp": "防御力%",
    "HealUp": "治疗加成", "HPMaxAdd": "生命值", "HPMaxUp": "生命值%",
    "MagBase": "环合强度", "UnbalIntensityBase": "倾陷强度",
}
_OFFICIAL_SHAPE_LABELS = {
    "hen2": "H_2", "hen3": "H_3", "hen4": "H_4", "shu2": "V_2",
    "shu3": "V_3", "shu4": "V_4", "z3": "Trap_4_H", "z4": "Trap_4_V",
    "zhijiao1": "L_3_BL", "zhijiao2": "L_3_TL", "zhijiao3": "L_3_TR",
    "zhijiao4": "L_3_BR",
}


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _page_warehouse(self):
    """Create the virtualized official-inventory page without loading items yet."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)

    title_row = QHBoxLayout()
    title = QLabel("仓库")
    title.setStyleSheet(themed_style("font-size:18px;font-weight:700;color:#f0f6fc"))
    title_row.addWidget(title)
    self.warehouse_summary = QLabel("读取背包稳定快照中…")
    self.warehouse_summary.setStyleSheet(themed_style("color:#8b949e;margin-left:8px"))
    title_row.addWidget(self.warehouse_summary)
    title_row.addStretch()
    self.warehouse_save_btn = QPushButton("保存")
    self.warehouse_save_btn.setObjectName("btnPrimary")
    self.warehouse_save_btn.setStyleSheet(themed_style(
        "QPushButton{background:#1f6feb;border-color:#388bfd;color:white;}"
        "QPushButton:hover{background:#388bfd;}"
        "QPushButton:disabled{background:#30363d;color:#8b949e;}"
    ))
    self.warehouse_save_btn.setToolTip("将手动修改的弃置/锁定状态写入游戏")
    self.warehouse_save_btn.setEnabled(True)
    self.warehouse_save_btn.clicked.connect(self._save_warehouse_state_changes)
    title_row.addWidget(self.warehouse_save_btn)
    self.warehouse_manage_btn = QPushButton("管理")
    self.warehouse_manage_btn.setObjectName("btnPrimary")
    self.warehouse_manage_btn.setToolTip("按管理规则一键同步弃置/锁定状态")
    self.warehouse_manage_btn.clicked.connect(self._open_warehouse_state_manager)
    title_row.addWidget(self.warehouse_manage_btn)
    layout.addLayout(title_row)

    filters = QHBoxLayout()
    filters.setSpacing(8)
    self.warehouse_search = QLineEdit()
    self.warehouse_search.setPlaceholderText("搜索装备、套装、词条或已装备角色名…")
    self.warehouse_search.setClearButtonEnabled(True)
    self.warehouse_search.setMinimumWidth(280)
    self.warehouse_search.textChanged.connect(self._apply_warehouse_filters)
    filters.addWidget(self.warehouse_search, 1)
    self.warehouse_category_filter = QComboBox()
    self.warehouse_category_filter.addItem("全部类别", "all")
    self.warehouse_category_filter.addItem("驱动", "module")
    self.warehouse_category_filter.addItem("卡带", "core")
    self.warehouse_category_filter.currentIndexChanged.connect(lambda *_args: _on_warehouse_category_changed(self))
    filters.addWidget(self.warehouse_category_filter)
    self.warehouse_type_filter = QComboBox()
    self.warehouse_type_filter.addItem("全部类型", "all")
    self.warehouse_type_filter.currentIndexChanged.connect(self._apply_warehouse_filters)
    filters.addWidget(self.warehouse_type_filter)
    self.warehouse_quality_filter = QComboBox()
    self.warehouse_quality_filter.addItem("全部品质", "all")
    self.warehouse_quality_filter.addItem("金色", "gold")
    self.warehouse_quality_filter.addItem("紫色", "purple")
    self.warehouse_quality_filter.addItem("蓝色", "blue")
    self.warehouse_quality_filter.currentIndexChanged.connect(self._apply_warehouse_filters)
    filters.addWidget(self.warehouse_quality_filter)
    self.warehouse_status_filter = QComboBox()
    self.warehouse_status_filter.addItem("全部状态", "all")
    self.warehouse_status_filter.addItem("未装备", "unequipped")
    self.warehouse_status_filter.addItem("已装备", "equipped")
    self.warehouse_status_filter.addItem("已锁定", "locked")
    self.warehouse_status_filter.addItem("已弃置", "discarded")
    self.warehouse_status_filter.currentIndexChanged.connect(self._apply_warehouse_filters)
    filters.addWidget(self.warehouse_status_filter)
    layout.addLayout(filters)

    state_row = QHBoxLayout()
    state_row.setSpacing(8)
    self.warehouse_selection_label = QLabel("选中 0 件")
    self.warehouse_selection_label.setStyleSheet(themed_style("color:#8b949e"))
    state_row.addWidget(self.warehouse_selection_label)
    state_row.addWidget(QLabel("手动状态："))
    multi_select_hint = QLabel("（按住 CTRL 多选）")
    multi_select_hint.setStyleSheet(themed_style("color:#8b949e"))
    state_row.addWidget(multi_select_hint)
    self.warehouse_normal_btn = QPushButton("正常")
    self.warehouse_lock_btn = QPushButton("锁定")
    self.warehouse_discard_btn = QPushButton("弃置")
    for button, target_state in (
        (self.warehouse_normal_btn, "normal"),
        (self.warehouse_lock_btn, "locked"),
        (self.warehouse_discard_btn, "discarded"),
    ):
        button.setObjectName("btnAction")
        button.setEnabled(False)
        button.clicked.connect(lambda _checked=False, target=target_state: self._set_warehouse_selected_state(target))
        state_row.addWidget(button)
    state_row.addStretch()
    layout.addLayout(state_row)

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
    self.warehouse_view.setStyleSheet(themed_style("#warehouseView{background:#0d1117;border:1px solid #21262d;border-radius:10px;padding:8px}"))
    layout.addWidget(self.warehouse_view, 1)
    self.warehouse_hint = QLabel("仓库将在打开此页面时读取最新稳定背包快照。")
    self.warehouse_hint.setAlignment(Qt.AlignCenter)
    self.warehouse_hint.setStyleSheet(themed_style("color:#8b949e;padding:8px"))
    layout.addWidget(self.warehouse_hint)
    self._warehouse_all_items = []
    self._warehouse_snapshot_id = None
    self._warehouse_source = None
    self._warehouse_pending_state_changes = {}
    self._warehouse_base_states = {}
    self._warehouse_compare_first = None
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
    self.warehouse_hint.setText("正在读取背包稳定快照…")
    self.warehouse_hint.show()
    self.warehouse_summary.setText("读取中…")
    worker = WorkerThread(target=lambda: load_warehouse_snapshot(runtime.USER_DATABASE_PATH), parent=self)
    self._warehouse_load_worker = worker
    worker.result_ready.connect(lambda result, current=token: _on_warehouse_loaded(self, current, result))
    worker.error.connect(lambda error, current=token: _on_warehouse_load_error(self, current, error))
    worker.start()


def _on_warehouse_loaded(self, token, result):
    if token is not getattr(self, "_warehouse_load_token", None):
        return
    self._warehouse_snapshot_id = result.get("snapshot_id")
    self._warehouse_source = str(result.get("source") or "")
    self._warehouse_all_items = list(result.get("items") or [])
    self._warehouse_pending_state_changes = {}
    self._warehouse_base_states = {
        str(item.get("uid")): "discarded" if item.get("discarded") else "locked" if item.get("locked") else "normal"
        for item in self._warehouse_all_items if item.get("state_known", True)
    }
    self._warehouse_compare_first = None
    _refresh_warehouse_type_filter(self)
    self._apply_warehouse_filters()
    if self._warehouse_source == "gamepad":
        self.warehouse_hint.setText("当前为全量扫描库存：等级、锁定/弃置状态和已装备角色无法识别；鉴定与对比仍可使用。")
        self.warehouse_hint.show()


def _on_warehouse_load_error(self, token, error):
    if token is not getattr(self, "_warehouse_load_token", None):
        return
    self._warehouse_all_items = []
    self.warehouse_model.set_items([])
    self.warehouse_summary.setText("读取失败")
    self.warehouse_hint.setText(f"仓库读取失败：{error}")
    self.warehouse_hint.show()
    logger.error(f"读取仓库稳定快照失败: {error}")


def _apply_warehouse_filters(self):
    if not hasattr(self, "warehouse_model"):
        return
    filtered = filter_warehouse_items(
        getattr(self, "_warehouse_all_items", []),
        search=self.warehouse_search.text(),
        kind=str(self.warehouse_category_filter.currentData() or "all"),
        quality=str(self.warehouse_quality_filter.currentData() or "all"),
        status=str(self.warehouse_status_filter.currentData() or "all"),
        item_type=str(self.warehouse_type_filter.currentData() or "all"),
    )
    self.warehouse_model.set_items(filtered)
    total = len(getattr(self, "_warehouse_all_items", []))
    snapshot_id = getattr(self, "_warehouse_snapshot_id", None)
    snapshot_text = f"快照 #{snapshot_id} · " if snapshot_id is not None else ""
    self.warehouse_summary.setText(f"{snapshot_text}显示 {len(filtered)} / {total} 件")
    if filtered:
        self.warehouse_hint.hide()
    else:
        self.warehouse_hint.setText("当前筛选条件下没有装备。请先完成背包同步，或调整筛选条件。")
        self.warehouse_hint.show()


def _on_warehouse_category_changed(self) -> None:
    _refresh_warehouse_type_filter(self)
    self._apply_warehouse_filters()


def _refresh_warehouse_type_filter(self) -> None:
    """Link visible types to the selected category using the already loaded snapshot."""
    combo = getattr(self, "warehouse_type_filter", None)
    if combo is None:
        return
    current = combo.currentData()
    category = str(getattr(self, "warehouse_category_filter", combo).currentData() or "all")
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("全部类型", "all")
    for key, label in warehouse_type_options(getattr(self, "_warehouse_all_items", []), category):
        combo.addItem(label, key)
    index = combo.findData(current)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


def _on_warehouse_sync_state(self, state):
    """Refresh from a later stable snapshot unless the user has local edits."""
    if not hasattr(self, "warehouse_model") or getattr(state, "phase", None) != "listening":
        return
    snapshot_id = getattr(state, "last_snapshot_id", None)
    if not isinstance(snapshot_id, int) or snapshot_id == getattr(self, "_warehouse_snapshot_id", None):
        return
    if getattr(self, "_warehouse_pending_state_changes", {}):
        self.warehouse_hint.setText("游戏背包已有新快照；请先保存当前手动修改，仓库随后会自动刷新。")
        self.warehouse_hint.show()
        return
    self._refresh_warehouse()


def _on_warehouse_selection_changed(self, *_args):
    if not hasattr(self, "warehouse_view"):
        return
    indexes = self.warehouse_view.selectionModel().selectedIndexes()
    count = len(indexes)
    state_available = bool(indexes) and all(
        isinstance(index.data(Qt.UserRole), dict) and index.data(Qt.UserRole).get("state_known", True)
        for index in indexes
    )
    if hasattr(self, "warehouse_selection_label"):
        self.warehouse_selection_label.setText(f"选中 {count} 件")
    for name in ("warehouse_normal_btn", "warehouse_lock_btn", "warehouse_discard_btn"):
        button = getattr(self, name, None)
        if button is not None:
            button.setEnabled(state_available)


def _set_warehouse_selected_state(self, target_state: str):
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
        item = index.data(Qt.UserRole)
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
        warehouse_item_with_state(item, pending.get(str(item.get("uid")), "discarded" if item.get("discarded") else "locked" if item.get("locked") else "normal"))
        if str(item.get("uid")) in changed_uids else item
        for item in self._warehouse_all_items
    ]
    self._apply_warehouse_filters()
    self._on_warehouse_selection_changed()
    self._update_warehouse_save_state()


def _toggle_warehouse_item_state(self, index, target_state: str):
    """Stage a single card's lock/discard icon action without changing game state yet."""
    item = index.data(Qt.UserRole) if index is not None else None
    if not isinstance(item, dict) or not item.get("state_known", True) or target_state not in {"normal", "locked", "discarded"}:
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


def _show_warehouse_item_identification(self, index):
    """Evaluate one warehouse card against matching role rules in a worker."""
    item = index.data(Qt.UserRole) if index is not None else None
    snapshot_id = getattr(self, "_warehouse_snapshot_id", None)
    if not isinstance(item, dict) or not isinstance(snapshot_id, int):
        return
    active_worker = getattr(self, "_warehouse_identification_worker", None)
    if active_worker is not None and active_worker.isRunning():
        return
    service = WarehouseIdentificationService(runtime.USER_DATABASE_PATH)
    worker = WorkerThread(
        target=lambda: self._run_identify_item(service.load_item(snapshot_id, str(item.get("uid") or ""))),
        parent=self,
    )
    self._warehouse_identification_worker = worker
    worker.result_ready.connect(lambda result, current=dict(item): _show_warehouse_identification_dialog(self, current, result))
    worker.error.connect(lambda error: QMessageBox.warning(self, "装备鉴定失败", f"未能完成角色匹配评分：\n{error}"))
    worker.start()


def _select_warehouse_compare_item(self, index) -> None:
    """Remember the left card, then compare another item in the same category."""
    item = index.data(Qt.UserRole) if index is not None else None
    snapshot_id = getattr(self, "_warehouse_snapshot_id", None)
    if not isinstance(item, dict) or not isinstance(snapshot_id, int):
        return
    category = warehouse_item_compare_category(item)
    first = getattr(self, "_warehouse_compare_first", None)
    if first is None:
        self._warehouse_compare_first = {"item": dict(item), "category": category}
        self.warehouse_hint.setText(f"已选择左栏 [{item.get('display_name') or item.get('title')}]；请选择同类别装备作为右栏。")
        self.warehouse_hint.show()
        return
    first_item = first["item"]
    if str(first_item.get("uid")) == str(item.get("uid")):
        QMessageBox.information(self, "装备对比", "请再选择另一件同类别装备进行对比。")
        return
    if first["category"] != category:
        QMessageBox.warning(self, "装备对比", "驱动和卡带不能互相对比；请选择同类别装备。")
        return
    active_worker = getattr(self, "_warehouse_identification_worker", None)
    if active_worker is not None and active_worker.isRunning():
        return
    self._warehouse_compare_first = None
    service = WarehouseIdentificationService(runtime.USER_DATABASE_PATH)
    worker = WorkerThread(
        target=lambda: (
            self._run_identify_item(service.load_item(snapshot_id, str(first_item.get("uid") or ""))),
            self._run_identify_item(service.load_item(snapshot_id, str(item.get("uid") or ""))),
        ),
        parent=self,
    )
    self._warehouse_identification_worker = worker
    worker.result_ready.connect(
        lambda result, left=dict(first_item), right=dict(item): _show_warehouse_identification_comparison(self, left, right, result)
    )
    worker.error.connect(lambda error: QMessageBox.warning(self, "装备对比失败", f"未能完成同类别装备鉴定对比：\n{error}"))
    worker.start()


def _show_warehouse_identification_dialog(self, item: dict, result: dict) -> None:
    """Show only the reusable role-score results in a fixed, scrollable dialog."""
    dialog = QDialog(self)
    dialog.setWindowTitle("装备鉴定结果")
    dialog.setFixedSize(560, 520)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 12)
    layout.setSpacing(10)

    scroll = QScrollArea(dialog)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    content = QWidget()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(2, 2, 2, 2)
    content_layout.setSpacing(8)
    match_group = QGroupBox("匹配角色评分")
    match_layout = QVBoxLayout(match_group)
    match_layout.setSpacing(8)
    rows = list(result.get("rows") or []) if isinstance(result, dict) else []
    if rows:
        for rank, row in enumerate(rows, start=1):
            match_layout.addWidget(build_identify_result_row(rank, row))
    else:
        empty = QLabel("没有找到图纸可使用该装备的角色。")
        empty.setStyleSheet(themed_style("color:#8b949e"))
        match_layout.addWidget(empty)
    content_layout.addWidget(match_group)
    content_layout.addStretch()
    scroll.setWidget(content)
    layout.addWidget(scroll, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()


def _show_warehouse_identification_comparison(self, left: dict, right: dict, results: tuple[dict, dict]) -> None:
    """Show the first selected item at left and the compatible second item at right."""
    dialog = QDialog(self)
    dialog.setWindowTitle("装备鉴定对比")
    dialog.setFixedSize(1120, 620)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 12)
    columns = QHBoxLayout()
    columns.setSpacing(12)
    for title, item, result in (("左栏", left, results[0]), ("右栏", right, results[1])):
        group = QGroupBox(f"{title} · {item.get('display_name') or item.get('title')}")
        group_layout = QVBoxLayout(group)
        scroll = QScrollArea(group)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 2, 2, 2)
        rows = list(result.get("rows") or []) if isinstance(result, dict) else []
        if rows:
            for rank, row in enumerate(rows, start=1):
                content_layout.addWidget(build_identify_result_row(rank, row))
        else:
            empty = QLabel("没有找到图纸可使用该装备的角色。")
            empty.setStyleSheet(themed_style("color:#8b949e"))
            content_layout.addWidget(empty)
        content_layout.addStretch()
        scroll.setWidget(content)
        group_layout.addWidget(scroll)
        columns.addWidget(group, 1)
    layout.addLayout(columns, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()


def _update_warehouse_save_state(self):
    pending_count = len(getattr(self, "_warehouse_pending_state_changes", {}))
    if hasattr(self, "warehouse_save_btn"):
        self.warehouse_save_btn.setEnabled(True)
        self.warehouse_save_btn.setText(f"保存 ({pending_count})" if pending_count else "保存")


def _save_warehouse_state_changes(self):
    """Validate manual card edits against the fixed snapshot, then write via nte-core."""
    pending = dict(getattr(self, "_warehouse_pending_state_changes", {}))
    snapshot_id = getattr(self, "_warehouse_snapshot_id", None)
    if not pending:
        QMessageBox.information(self, "仓库保存", "没有待保存的弃置/锁定状态修改。")
        return
    if not isinstance(snapshot_id, int):
        return
    if getattr(self, "_warehouse_source", "") != "nte_core":
        QMessageBox.information(self, "仓库状态不可用", "全量扫描库存无法读取或修改锁定、弃置状态；请先获取背包同步快照。")
        return
    active_worker = getattr(self, "_warehouse_state_worker", None)
    if active_worker is not None and active_worker.isRunning():
        return
    sync_service = getattr(self, "_inventory_sync_service", None)
    if sync_service is None or not sync_service.is_running:
        QMessageBox.warning(self, "无法保存仓库状态", "请先在工作台启动背包同步，并等待状态显示为稳定监听。")
        return
    service = WarehouseStateManagementService(runtime.USER_DATABASE_PATH, sync_service)
    self._warehouse_state_service = service
    self._set_warehouse_management_busy(True, "正在检查手动修改…")
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
        QMessageBox.information(self, "仓库保存", "所有手动状态已与当前游戏背包一致。")
        return
    counts = {"弃置": 0, "锁定": 0, "正常": 0}
    for change in plan.changes:
        counts[{"discarded": "弃置", "locked": "锁定", "normal": "正常"}[change["target_state"]]] += 1
    message = (
        f"将保存 {len(plan.changes)} 件装备的手动状态：弃置 {counts['弃置']} 件，"
        f"锁定 {counts['锁定']} 件，恢复正常 {counts['正常']} 件。\n\n"
        "确认后会通过本地核心组件直接写入游戏。"
    )
    if QMessageBox.question(self, "确认保存仓库状态", message, QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
        return
    service = self._warehouse_state_service
    self._set_warehouse_management_busy(True, "正在保存弃置/锁定状态到游戏…")
    worker = WorkerThread(target=lambda: service.apply(plan), parent=self)
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
        QMessageBox.information(self, "仓库管理不可用", "全量扫描库存无法读取或修改锁定、弃置状态；请先获取背包同步快照。")
        return
    selected_roles = self.role_selector.get_selected() if hasattr(self, "role_selector") else []
    if not show_scan_post_action_dialog(self, runtime.USER_CONFIG_DIR, selected_roles):
        return
    config = load_scan_post_action_config(runtime.USER_CONFIG_DIR)
    error = validate_post_action_config(config, selected_roles)
    if error:
        QMessageBox.warning(self, "管理配置无效", error)
        return
    sync_service = getattr(self, "_inventory_sync_service", None)
    if sync_service is None or not sync_service.is_running:
        QMessageBox.warning(self, "无法管理仓库", "请先在工作台启动背包同步，并等待状态显示为稳定监听。")
        return
    service = WarehouseStateManagementService(
        runtime.USER_DATABASE_PATH,
        sync_service,
        config_dir=runtime.CONFIG_DIR,
    )
    self._warehouse_state_service = service
    self._set_warehouse_management_busy(True, "正在计算弃置/锁定目标…")
    worker = WorkerThread(target=lambda: service.evaluate(config, selected_roles), parent=self)
    self._warehouse_state_worker = worker
    worker.result_ready.connect(self._on_warehouse_state_plan_ready)
    worker.error.connect(self._on_warehouse_state_error)
    worker.start()


def _on_warehouse_state_plan_ready(self, plan):
    self._set_warehouse_management_busy(False)
    if not plan.changes:
        QMessageBox.information(self, "仓库管理", "当前稳定背包没有符合规则、需要变更状态的装备。")
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
    message = (
        f"将按快照 #{plan.snapshot_id} 操作 {len(plan.changes)} 件装备：\n"
        f"弃置 {counts['弃置']} 件，锁定 {counts['锁定']} 件，"
        f"取消状态 {counts['取消弃置/锁定']} 件。\n\n"
        "确认后会通过本地核心组件直接写入游戏。"
    )
    if QMessageBox.question(self, "确认一键管理", message, QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
        return
    service = self._warehouse_state_service
    self._set_warehouse_management_busy(True, "正在通过本地核心组件同步弃置/锁定状态…")
    worker = WorkerThread(target=lambda: service.apply(plan), parent=self)
    self._warehouse_state_worker = worker
    worker.result_ready.connect(self._on_warehouse_state_applied)
    worker.error.connect(self._on_warehouse_state_error)
    worker.start()


def _on_warehouse_state_applied(self, result):
    self._set_warehouse_management_busy(False)
    summary = result.summary
    QMessageBox.information(
        self,
        "仓库管理完成",
        f"已完成弃置/锁定操作：弃置 {summary['discard_set_count']} 件，"
        f"锁定 {summary['lock_set_count']} 件，"
        f"取消弃置 {summary['discard_clear_count']} 件，"
        f"取消锁定 {summary['lock_clear_count']} 件。",
    )
    self._warehouse_pending_state_changes = {}
    self._warehouse_base_states = {
        str(item.get("uid")): "discarded" if item.get("discarded") else "locked" if item.get("locked") else "normal"
        for item in getattr(self, "_warehouse_all_items", [])
    }
    self._update_warehouse_save_state()


def _on_warehouse_state_error(self, error):
    self._set_warehouse_management_busy(False)
    logger.error(f"仓库状态管理失败: {error}")
    QMessageBox.critical(self, "仓库管理失败", f"未能完成一键弃置/锁定：\n{error}")


def _set_warehouse_management_busy(self, busy: bool, hint: str = ""):
    if hasattr(self, "warehouse_manage_btn"):
        self.warehouse_manage_btn.setEnabled(not busy)
    if hasattr(self, "warehouse_save_btn"):
        self.warehouse_save_btn.setEnabled(not busy)
    for name in ("warehouse_normal_btn", "warehouse_lock_btn", "warehouse_discard_btn"):
        button = getattr(self, name, None)
        if button is not None:
            button.setEnabled(not busy and bool(self.warehouse_view.selectionModel().selectedIndexes()))
    if busy and hasattr(self, "warehouse_hint"):
        self.warehouse_hint.setText(hint)
        self.warehouse_hint.show()


