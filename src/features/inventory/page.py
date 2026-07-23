# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QGroupBox, QHBoxLayout, QLabel, QInputDialog, QLineEdit, QListView, QMessageBox, QPushButton, QScrollArea, \
    QVBoxLayout, QWidget

from src.app import runtime
from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.app.theme import GRADE_COLORS, theme_color, theme_rgba, themed_style
from src.app.workers import WorkerThread
from src.features.drive_assembly.ui_bridge import (
    execute_all_roles_from_current_game_page,
    execute_selected_role_from_current_game_page,
)
from src.features.role.replacement_service import (
    build_equipment_role_context,
    rank_replacement_candidates_by_damage,
)
from src.features.role.equipment_import import set_bonus_from_tape_source
from src.features.scanning.file_lifecycle import equipment_compare_signature
from src.features.inventory.warehouse import (
    WarehouseCardDelegate,
    WarehouseGridView,
    WarehouseInventoryModel,
    filter_warehouse_items,
    load_warehouse_snapshot,
    warehouse_item_compare_category,
    warehouse_item_with_state,
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

__all__ = ['_equipment_compare_signature', '_same_equipment_by_ocr', '_page_equipment', '_refresh_equip',
           '_page_warehouse', '_refresh_warehouse', '_apply_warehouse_filters', '_on_warehouse_sync_state',
           '_on_warehouse_selection_changed', '_set_warehouse_selected_state', '_toggle_warehouse_item_state', '_save_warehouse_state_changes',
           '_show_warehouse_item_identification', '_update_warehouse_save_state', '_on_warehouse_manual_plan_ready', '_open_warehouse_state_manager',
           '_on_warehouse_state_plan_ready', '_on_warehouse_state_applied', '_on_warehouse_state_error',
           '_set_warehouse_management_busy',
           '_saved_plan_diff_text', '_show_saved_plan_diff_dialog', '_clear_all_equipment', '_delete_role_equipment', '_optimize_saved_equipment',
           '_preview_assemble_role', '_preview_fast_assemble_all_roles', '_preview_automatic_assemble_all_roles']

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


def _equipment_compare_signature(self,item):
    return equipment_compare_signature(item)

def _same_equipment_by_ocr(self,left:Path,right:Path):
    return self._scan_lifecycle().same_equipment_by_ocr(left,right)

def _page_equipment(self):
    page=QWidget(); l=QVBoxLayout(page); l.setContentsMargins(20,16,20,16); l.setSpacing(8)
    sh=QHBoxLayout(); sh.addWidget(QLabel("搜索"))
    self.equip_search=QLineEdit(); self.equip_search.setPlaceholderText("搜索角色名称（支持拼音）..."); self.equip_search.setClearButtonEnabled(True)
    self._equip_search_timer = QTimer(page)
    self._equip_search_timer.setSingleShot(True)
    self._equip_search_timer.setInterval(120)
    self._equip_search_timer.timeout.connect(self._refresh_equip)
    self.equip_search.textChanged.connect(lambda _text: self._equip_search_timer.start()); sh.addWidget(self.equip_search, 1)
    clear_btn=QPushButton("清空配装"); clear_btn.setObjectName("btnDanger"); clear_btn.clicked.connect(self._clear_all_equipment)
    sh.addWidget(clear_btn)
    fast_btn=QPushButton("极速装配"); fast_btn.setObjectName("btnPrimary"); fast_btn.clicked.connect(self._preview_fast_assemble_all_roles)
    fast_btn.setToolTip("通过游戏内装备插件直接写入已保存方案")
    sh.addWidget(fast_btn)
    automatic_btn=QPushButton("自动装配"); automatic_btn.setObjectName("btnPrimary"); automatic_btn.clicked.connect(self._preview_automatic_assemble_all_roles)
    automatic_btn.setToolTip("模拟游戏内操作，逐步完成已保存方案")
    sh.addWidget(automatic_btn)
    l.addLayout(sh)
    scroll=QScrollArea(); scroll.setWidgetResizable(True)
    self.equip_content=QWidget(); self.equip_content_layout=QVBoxLayout(self.equip_content); scroll.setWidget(self.equip_content)
    l.addWidget(scroll,1); return page


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

def _clear_equip_content(self):
    while self.equip_content_layout.count():
        it=self.equip_content_layout.takeAt(0)
        if it.widget(): it.widget().deleteLater()

def _load_sqlite_equipment_display_states(database_path):
    """Read display-only saved plans off the UI thread."""
    with UserDataDao(database_path) as user_dao, StaticGameDataDao() as static_dao:
        plans = user_dao.list_active_loadout_plans_by_role()
        return {
            role_name: _sqlite_plan_display_state(plan, user_dao, static_dao)
            for role_name, plan in plans.items()
        }


def _queue_equipment_render(self, eq):
    all_roles=sorted(eq.keys())
    filt=self.equip_search.text().strip() if hasattr(self,'equip_search') else ""
    roles=[]
    for role_name in all_roles:
        if filt and not _match_pinyin(role_name,filt): continue
        rd=eq.get(role_name,{})
        if not isinstance(rd,dict): continue
        roles.append((role_name,rd))

    self._equip_render_token=object()
    token=self._equip_render_token
    self._equip_render_queue=roles
    self._equip_render_index=0
    self._equip_render_stretch_added=False

    if not roles:
        ph=QLabel("暂无已保存的配装。请先执行分配并保存。"); ph.setStyleSheet(themed_style("color:#6e7681;padding:24px")); ph.setAlignment(Qt.AlignCenter); self.equip_content_layout.addWidget(ph)
        self.equip_content_layout.addStretch()
        return

    _render_equip_batch(self, token, EQUIPMENT_INITIAL_RENDER_COUNT)


def _on_sqlite_equipment_display_loaded(self, token, eq):
    if token is not getattr(self, "_equip_load_token", None):
        return
    _clear_equip_content(self)
    _queue_equipment_render(self, eq if isinstance(eq, dict) else {})


def _on_sqlite_equipment_display_error(self, token, error):
    if token is not getattr(self, "_equip_load_token", None):
        return
    logger.error(f"刷新 SQLite 配装展示失败: {error}")
    _clear_equip_content(self)
    _queue_equipment_render(self, {})


def _refresh_equip(self):
    _clear_equip_content(self)
    # The production page may contain many plans and each requires snapshot
    # projection.  Keep database work off the Qt event loop; plain test hosts
    # retain the direct path below.
    if isinstance(self, QWidget):
        token=object()
        self._equip_load_token=token
        loading=QLabel("正在读取已保存的配装…")
        loading.setStyleSheet(themed_style("color:#8b949e;padding:24px"))
        loading.setAlignment(Qt.AlignCenter)
        self.equip_content_layout.addWidget(loading)
        worker=WorkerThread(target=lambda: _load_sqlite_equipment_display_states(runtime.USER_DATABASE_PATH), parent=self)
        self._equip_load_worker=worker
        worker.result_ready.connect(lambda eq, current=token: _on_sqlite_equipment_display_loaded(self, current, eq))
        worker.error.connect(lambda error, current=token: _on_sqlite_equipment_display_error(self, current, error))
        worker.start()
        return
    try:
        with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao, StaticGameDataDao() as static_dao:
            plans = user_dao.list_active_loadout_plans_by_role()
            eq = {
                role_name: _sqlite_plan_display_state(plan, user_dao, static_dao)
                for role_name, plan in plans.items()
            }
    except Exception as exc:
        logger.error(f"刷新 SQLite 配装展示失败: {exc}")
        eq = {}
    _queue_equipment_render(self, eq)


def _official_stat_values(stats):
    values={}
    for stat in stats or []:
        property_id=str(stat.get("property_id") or "")
        label=_OFFICIAL_STAT_LABELS.get(property_id, property_id or "未知属性")
        value=float(stat.get("value",0.0) or 0.0)
        if stat.get("percent"):
            value*=100.0
        values[label]=round(value,6)
    return values


def _display_shape_id(geometry):
    value=str(geometry or "").removeprefix("EquipmentGeometry_").casefold()
    return _OFFICIAL_SHAPE_LABELS.get(value, str(geometry or "未知形状"))


def _sqlite_plan_display_state(plan, user_dao, static_dao):
    """将活动 SQLite 方案转换为配装页展示模型；不读取旧 JSON。"""
    snapshot_id=int(plan["source_snapshot_id"])
    items={(row["uid_serial"],row["uid_slot"]): row for row in user_dao.list_inventory_items(snapshot_id)}
    shape_cells={shape["shape_id"]: shape.get("cells") or [] for shape in static_dao.list_shapes()}
    suit_names={str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"]) for suit in static_dao.list_suits()}
    payload=plan.get("payload") or {}
    last_diff=dict(payload.get("last_diff") or {})
    added_uids={str(uid) for uid in (last_diff.get(DIFF_ADDED_UIDS) or ()) if uid}
    changed_uids={str(uid) for uid in (payload.get("changed_uids") or ()) if uid}
    board=[["0" for _ in range(5)] for _ in range(5)]
    drives=[]
    tape=None
    for assignment in plan["assignments"]:
        item=items.get((assignment["uid_serial"],assignment["uid_slot"]))
        if item is None:
            continue
        raw=assignment.get("raw_assignment") or {}
        uid_prefix="module" if item["kind"] == "module" else "core"
        uid=f"nte-{uid_prefix}-{item['uid_slot']}-{item['uid_serial']}"
        if item["kind"] == "core":
            main_stats=_official_stat_values(item.get("main_stats"))
            tape={
                EQUIP_UID: uid, EQUIP_SET_NAME: suit_names.get(str(item.get("suit_id") or ""), str(item.get("suit_id") or "未知套装")),
                EQUIP_MAIN_STATS: next(iter(main_stats), "未知主词条"), EQUIP_SUB_STATS: _official_stat_values(item.get("sub_stats")),
                EQUIP_QUALITY: {"orange":"Gold","purple":"Purple","blue":"Blue"}.get(str(item.get("quality")).casefold(),"Gold"),
                "discarded": bool(item.get("discarded")),
                EQUIP_IS_CHANGED: uid in changed_uids,
                EQUIP_IS_NEW: uid in added_uids and uid not in changed_uids,
            }
            continue
        geometry=item.get("geometry")
        shape_id=_display_shape_id(geometry)
        drives.append({
            EQUIP_UID: uid, EQUIP_SHAPE_ID: shape_id, EQUIP_SUB_STATS: _official_stat_values(item.get("sub_stats")),
            EQUIP_QUALITY: {"orange":"Gold","purple":"Purple","blue":"Blue"}.get(str(item.get("quality")).casefold(),"Gold"),
            "discarded": bool(item.get("discarded")),
            # Snapshot ingestion derives duplicate state for modules.  Keep it
            # on the saved-plan view model so the card can show it alongside
            # discard/new/change state.
            "is_duplicate_drive": bool(item.get("is_duplicate_drive")),
            "duplicate_group_id": item.get("duplicate_group_id"),
            "duplicate_index": item.get("duplicate_index"),
            "duplicate_count": item.get("duplicate_count"),
            EQUIP_IS_CHANGED: uid in changed_uids,
            EQUIP_IS_NEW: uid in added_uids and uid not in changed_uids,
        })
        row,column=assignment.get("target_row"),assignment.get("target_column")
        official_shape="EquipmentGeometry_" + str(geometry or "").removeprefix("EquipmentGeometry_")
        for cell in shape_cells.get(official_shape,[]):
            target_row=int(row)+int(cell["x"])-1
            target_column=int(column)+int(cell["y"])-1
            if 0 <= target_row < 5 and 0 <= target_column < 5:
                board[target_row][target_column]=shape_id
    return {
        ROLE_BLUEPRINT_LAYOUT: board, ROLE_EQUIPPED_TAPE: tape, ROLE_EQUIPPED_DRIVES: drives,
        ROLE_TOTAL_SCORE: float(plan.get("score") or 0.0), ROLE_TOTAL_GRADE: "",
        "strategy_mode": payload.get("strategy", ""), "_sqlite_plan_id": plan["plan_id"],
        "_sqlite_source_snapshot_id": snapshot_id,
        ROLE_LAST_DIFF: last_diff,
    }


def _sqlite_inventory_item_display(row, suit_names):
    """Project one official snapshot item for the saved-plan replacement dialog."""
    kind = str(row.get("kind") or "")
    quality = {"orange": "Gold", "purple": "Purple", "blue": "Blue"}.get(
        str(row.get("quality") or "").casefold(), "Gold"
    )
    uid = f"nte-{'module' if kind == 'module' else 'core'}-{row.get('uid_slot')}-{row.get('uid_serial')}"
    if kind == "core":
        main_stats = _official_stat_values(row.get("main_stats"))
        return {
            EQUIP_UID: uid,
            EQUIP_SET_NAME: suit_names.get(str(row.get("suit_id") or ""), str(row.get("suit_id") or "未知套装")),
            EQUIP_MAIN_STATS: next(iter(main_stats), "未知主词条"),
            EQUIP_SUB_STATS: _official_stat_values(row.get("sub_stats")),
            EQUIP_QUALITY: quality,
            "_role_main_stats": main_stats,
            "_item_id": str(row.get("item_id") or ""),
            "_uid_serial": int(row.get("uid_serial") or 0),
            "_uid_slot": int(row.get("uid_slot") or 0),
        }
    return {
        EQUIP_UID: uid,
        EQUIP_SHAPE_ID: _display_shape_id(row.get("geometry")),
        EQUIP_SUB_STATS: _official_stat_values(row.get("sub_stats")),
        EQUIP_QUALITY: quality,
        "_item_id": str(row.get("item_id") or ""),
        "is_duplicate_drive": bool(row.get("is_duplicate_drive")),
        "duplicate_group_id": row.get("duplicate_group_id"),
        "duplicate_index": row.get("duplicate_index"),
        "duplicate_count": row.get("duplicate_count"),
        "_uid_serial": int(row.get("uid_serial") or 0),
        "_uid_slot": int(row.get("uid_slot") or 0),
    }


def _replacement_item_icon(asset_catalog, item_kind, item):
    """Resolve the packaged official core or module image for a replacement card."""
    if asset_catalog is None:
        return None
    item_id = str(item.get("_item_id") or "")
    kind = "module" if item_kind == "drive" else "core"
    return asset_catalog.inventory_item_icon(kind, item_id) if item_id else None


def _replacement_assignments(plan, old_uid, replacement):
    """Copy immutable plan assignments while replacing exactly one native UID."""
    replacement_serial = int(replacement["_uid_serial"])
    replacement_slot = int(replacement["_uid_slot"])
    assignments = []
    replaced = False
    for source in plan.get("assignments") or []:
        assignment = dict(source)
        uid = f"nte-{'module' if assignment.get('kind') == 'module' else 'core'}-{assignment.get('uid_slot')}-{assignment.get('uid_serial')}"
        if uid == str(old_uid):
            assignment["uid_serial"] = replacement_serial
            assignment["uid_slot"] = replacement_slot
            raw_assignment = dict(assignment.get("raw_assignment") or {})
            raw_assignment["uid"] = {"serial": replacement_serial, "slot": replacement_slot}
            assignment["raw_assignment"] = raw_assignment
            replaced = True
        assignments.append(assignment)
    if not replaced:
        raise ValueError("当前装备已变化，请刷新配装页面后重试。")
    return assignments


def _active_sqlite_equipment_users(user_dao, excluded_role_name: str) -> dict[tuple[str, int, int], tuple[str, ...]]:
    """Map native equipment UIDs to other active SQLite loadout roles once."""
    users: dict[tuple[str, int, int], list[str]] = {}
    for role_name, plan in user_dao.list_active_loadout_plans_by_role().items():
        if role_name == excluded_role_name:
            continue
        for assignment in plan.get("assignments") or []:
            kind = str(assignment.get("kind") or "")
            if kind not in {"module", "core"}:
                continue
            try:
                key = (kind, int(assignment["uid_serial"]), int(assignment["uid_slot"]))
            except (KeyError, TypeError, ValueError):
                continue
            role_users = users.setdefault(key, [])
            if role_name not in role_users:
                role_users.append(role_name)
    return {key: tuple(names) for key, names in users.items()}


def _sqlite_replacement_candidates(database_path, role_name, item_kind, old_uid):
    """Read compatible alternatives from the active plan's immutable snapshot."""
    with UserDataDao(database_path) as user_dao, StaticGameDataDao() as static_dao:
        plan = user_dao.get_active_loadout_plan_for_role(role_name)
        if plan is None:
            raise ValueError("未找到该角色的已保存方案")
        snapshot_id = int(plan["source_snapshot_id"])
        rows = user_dao.list_inventory_items(snapshot_id)
        suit_names = {str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"]) for suit in static_dao.list_suits()}
        displays = [_sqlite_inventory_item_display(row, suit_names) for row in rows]
        items_by_key = {
            (int(item["_uid_serial"]), int(item["_uid_slot"])): item
            for item in displays
        }
        current = next((item for item in displays if str(item.get(EQUIP_UID)) == str(old_uid)), None)
        if current is None:
            raise ValueError("当前装备不在该方案绑定的背包快照中")
        expected_kind = "module" if item_kind == "drive" else "core"
        assigned = {
            (int(assignment["uid_serial"]), int(assignment["uid_slot"]))
            for assignment in plan.get("assignments") or []
            if str(assignment.get("kind")) == expected_kind
        }
        equipped_by_roles = _active_sqlite_equipment_users(user_dao, role_name)
        old_key = (int(current["_uid_serial"]), int(current["_uid_slot"]))
        assigned_items = [
            items_by_key[(int(assignment["uid_serial"]), int(assignment["uid_slot"]))]
            for assignment in plan.get("assignments") or []
            if (int(assignment["uid_serial"]), int(assignment["uid_slot"])) in items_by_key
        ]
        plan_drives = [item for item in assigned_items if item.get(EQUIP_SHAPE_ID)]
        plan_tape = next((item for item in assigned_items if item.get(EQUIP_SET_NAME)), None)
        candidates = []
        for row, item in zip(rows, displays):
            if str(row.get("kind")) != expected_kind:
                continue
            item_key = (int(item["_uid_serial"]), int(item["_uid_slot"]))
            if item_key == old_key or item_key in assigned:
                continue
            if item_kind == "drive" and item.get(EQUIP_SHAPE_ID) != current.get(EQUIP_SHAPE_ID):
                continue
            if item_kind == "tape" and item.get(EQUIP_SET_NAME) != current.get(EQUIP_SET_NAME):
                continue
            candidate = dict(item)
            candidate["_used_by"] = equipped_by_roles.get(
                (expected_kind, int(candidate["_uid_serial"]), int(candidate["_uid_slot"])), ()
            )
            candidates.append(candidate)
        return plan, current, candidates, plan_drives, plan_tape


def _saved_plan_optimization_role_context(self, role_name: str) -> dict:
    """Load the same role-page panel context before evaluating replacement DPS."""

    form_data = getattr(self, "_my_role_form_data", None)
    if not getattr(self, "_my_role_dirty", False):
        # The role page owns the captured account panel/base/weapon data.  It is
        # lazily loaded, so a user who opens 配装 first previously got an empty
        # context and severely inflated flat-attack candidates.
        refresh = getattr(self, "_refresh_my_role", None)
        if callable(refresh):
            refresh()
        form_data = getattr(self, "_my_role_form_data", None)
    else:
        # Preserve unsaved edits while ensuring focused numeric text is applied.
        flush = getattr(self, "_flush_role_widgets", None)
        if callable(flush):
            flush()
        form_data = getattr(self, "_my_role_form_data", None)

    role_context = form_data.get(role_name) if isinstance(form_data, dict) else None
    if not isinstance(role_context, dict):
        raise ValueError(f"角色 [{role_name}] 的角色面板尚未加载，无法计算直伤收益")
    return role_context


def _optimize_saved_equipment(
    self,
    role_name: str,
    item_kind: str,
    uid: str,
    *,
    weights_override: dict[str, float] | None = None,
    main_weights_override: dict[str, float] | None = None,
    rank_by_damage: bool = True,
    after_replace=None,
    core_term: str = "卡带",
    assignment_scores_override: dict[str, float] | None = None,
    exclude_used_by_others: bool = False,
    replacement_persister=None,
):
    """Restore per-card optimization using only the active SQLite plan snapshot."""
    try:
        plan, current, candidates, plan_drives, plan_tape = _sqlite_replacement_candidates(
            runtime.USER_DATABASE_PATH, role_name, item_kind, uid
        )
    except Exception as exc:
        QMessageBox.warning(self, "优化替换", str(exc))
        return
    if exclude_used_by_others:
        candidates = [candidate for candidate in candidates if not candidate.get("_used_by")]
    role_cfg = {}
    if not isinstance(weights_override, dict) or not isinstance(main_weights_override, dict):
        role_cfg = (getattr(self, "roles_db", {}) or {}).get(role_name, {})
    weights = (
        dict(weights_override)
        if isinstance(weights_override, dict)
        else role_cfg.get("weights", {})
    )
    main_weights = (
        dict(main_weights_override)
        if isinstance(main_weights_override, dict)
        else role_cfg.get("main_weights")
    )
    if item_kind == "drive":
        score = lambda item: float(self._score_drive_dict(item.get(EQUIP_SUB_STATS, {}), item.get(EQUIP_SHAPE_ID, ""), weights, item.get(EQUIP_QUALITY, "Gold")))
        title = f"优化替换 - {current.get(EQUIP_SHAPE_ID) or '驱动'}"
    else:
        score = lambda item: float(self._score_tape_dict(item.get(EQUIP_MAIN_STATS, ""), item.get(EQUIP_SUB_STATS, {}), weights, item.get(EQUIP_QUALITY, "Gold"), main_weights))
        title = f"替换{core_term} - {current.get(EQUIP_SET_NAME) or core_term}"
    current_score = score(current)
    role_drives = [
        {
            "uid": item.get(EQUIP_UID, ""),
            "shape_id": item.get(EQUIP_SHAPE_ID, ""),
            "sub_stats": dict(item.get(EQUIP_SUB_STATS) or {}),
            "quality": item.get(EQUIP_QUALITY, "Gold"),
        }
        for item in plan_drives
    ]
    role_tape = {
        "uid": plan_tape.get(EQUIP_UID, ""),
        "set_name": plan_tape.get(EQUIP_SET_NAME, ""),
        "main_stats": dict(plan_tape.get("_role_main_stats") or {}),
        "sub_stats": dict(plan_tape.get(EQUIP_SUB_STATS) or {}),
        "quality": plan_tape.get(EQUIP_QUALITY, "Gold"),
    } if plan_tape else {}
    if rank_by_damage:
        try:
            role_base = _saved_plan_optimization_role_context(self, role_name)
        except ValueError as exc:
            QMessageBox.warning(self, "优化替换", str(exc))
            return
        role_set_bonus = (
            set_bonus_from_tape_source(role_tape)
            if role_tape else {"display_name": "", "skill": {}, "skill_2": {}, "skill_cover": 0.8}
        )
        role_context = build_equipment_role_context(
            role_base, role_drives, role_tape, set_bonus=role_set_bonus
        )
        if item_kind == "drive":
            direct_current = next((item for item in role_drives if item.get("uid") == uid), current)
            direct_candidates = [
                {"uid": item.get(EQUIP_UID, ""), "shape_id": item.get(EQUIP_SHAPE_ID, ""),
                 "sub_stats": dict(item.get(EQUIP_SUB_STATS) or {}), "quality": item.get(EQUIP_QUALITY, "Gold"),
                 "_display": item}
                for item in candidates
            ]
        else:
            direct_current = role_tape
            direct_candidates = [
                {"uid": item.get(EQUIP_UID, ""), "set_name": item.get(EQUIP_SET_NAME, ""),
                 "main_stats": dict(item.get("_role_main_stats") or {}),
                 "sub_stats": dict(item.get(EQUIP_SUB_STATS) or {}), "quality": item.get(EQUIP_QUALITY, "Gold"),
                 "_display": item}
                for item in candidates
            ]
        current_margin, ranked_damage = rank_replacement_candidates_by_damage(
            role_context, item_kind, direct_current, direct_candidates
        )
        ranked = [
            (margin, score(candidate["_display"]), candidate["_display"])
            for margin, candidate in ranked_damage
        ][:30]
    else:
        current_margin = None
        ranked = sorted(
            ((None, score(candidate), candidate) for candidate in candidates),
            key=lambda row: row[1], reverse=True,
        )[:30]
    if not ranked:
        QMessageBox.information(self, "优化替换", "当前快照中没有可替换的同类装备。")
        return

    # Keep the same current-item / candidate-list layout used by the 角色功能 page.
    # Only the visual structure is shared: all items below still come from one
    # stable SQLite snapshot and the replacement is saved as a SQLite plan.
    item_label = current.get(EQUIP_SHAPE_ID) if item_kind == "drive" else current.get(EQUIP_SET_NAME)
    asset_catalog = (
        None
        if item_kind == "drive"
        else GameUiAssetCatalog(runtime.ASSET_DIR / "game_ui")
    )
    dialog = QDialog(self)
    dialog.setWindowTitle(f"{role_name} · {title}")
    dialog.resize(850, 650)
    layout = QVBoxLayout(dialog)
    role_header = QLabel(f"装配角色：{role_name}")
    role_header.setStyleSheet(themed_style(
        "font-size:15px;font-weight:800;color:#4dd0e1;"
        "border:1px solid #4dd0e1;border-radius:7px;padding:5px 12px;"
        "background:rgba(77,208,225,0.10)"
    ))
    layout.addWidget(role_header)
    summary_text = (
        f"当前直伤收益：{current_margin:+.2f}%（候选按直伤收益排序）"
        if rank_by_damage else "候选按当前词条配装权重评分排序"
    )
    summary = QLabel(
        f"{summary_text}；仅显示同{('形状' if item_kind == 'drive' else '套装')}的候选装备，"
        "不会占用本方案其他已选装备。"
    )
    summary.setWordWrap(True)
    summary.setStyleSheet(themed_style("color:#8b949e"))
    layout.addWidget(summary)
    current_group = QGroupBox("当前驱动" if item_kind == "drive" else f"当前{core_term}")
    current_layout = QVBoxLayout(current_group)
    current_layout.addWidget(self._equip_card(
        item_label or core_term, current.get(EQUIP_MAIN_STATS, ""),
        current.get(EQUIP_SUB_STATS, {}), current.get(EQUIP_SHAPE_ID), current.get(EQUIP_UID, ""), weights,
        (current_score, self._calc_grade(current_score, 15 if item_kind == "tape" else self._shape_areas.get(current.get(EQUIP_SHAPE_ID, ""), 3))),
        current.get(EQUIP_QUALITY, "Gold"),
        is_duplicate_drive=item_kind == "drive" and bool(current.get("is_duplicate_drive")),
        main_weights=main_weights, card_variant="inventory",
        item_icon_path=_replacement_item_icon(asset_catalog, item_kind, current),
    ))
    layout.addWidget(current_group)
    candidates_group = QGroupBox(
        f"可替换{'驱动' if item_kind == 'drive' else core_term} ({len(ranked)}个)"
    )
    candidates_layout = QVBoxLayout(candidates_group)
    scroll = QScrollArea(candidates_group)
    scroll.setWidgetResizable(True)
    content = QWidget()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(8)
    for candidate_margin, candidate_score, candidate in ranked:
        def apply_replacement(_checked=False, selected=candidate, selected_score=candidate_score):
            try:
                assignments = _replacement_assignments(plan, uid, selected)
                # This is an explicit user replacement: show green CHANGE for
                # the incoming item, and keep a complete SQLite diff for the
                # button/dialog after the page is refreshed.
                replacement_diff = {
                    DIFF_CHANGED: True,
                    DIFF_ADDED_UIDS: [str(selected.get(EQUIP_UID) or "")],
                    DIFF_ADDED: [{EQUIP_UID: str(selected.get(EQUIP_UID) or "")}],
                    DIFF_REMOVED: [{EQUIP_UID: str(current.get(EQUIP_UID) or "")}],
                }
                replacement_payload = dict(plan.get("payload") or {})
                replacement_payload["last_diff"] = replacement_diff
                replacement_payload["changed_uids"] = [str(selected.get(EQUIP_UID) or "")]
                assignment_scores = dict(
                    assignment_scores_override
                    if isinstance(assignment_scores_override, dict)
                    else replacement_payload.get("assignment_scores") or {}
                )
                assignment_scores.pop(str(uid), None)
                assignment_scores[str(selected.get(EQUIP_UID) or "")] = float(selected_score)
                replacement_payload["assignment_scores"] = assignment_scores
                if callable(replacement_persister):
                    replacement_persister(selected, selected_score, current_score)
                else:
                    with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
                        dao.save_loadout_plan(
                            name=str(plan.get("name") or f"优化方案：{role_name}"), character_id=int(plan["character_id"]),
                            assignments=assignments, source_snapshot_id=int(plan["source_snapshot_id"]), status="saved",
                            score=float(plan.get("score") or 0.0) - current_score + selected_score,
                            payload=replacement_payload, is_active=True,
                        )
            except Exception as exc:
                QMessageBox.warning(dialog, "替换失败", str(exc))
                return
            dialog.accept()
            self._refresh_equip()
            if callable(after_replace):
                after_replace(selected, selected_score, current_score)
            QMessageBox.information(self, "优化替换", "已保存为新的配装方案。")

        candidate_card = QWidget()
        candidate_layout = QVBoxLayout(candidate_card)
        candidate_layout.setContentsMargins(0, 0, 0, 0)
        candidate_layout.setSpacing(4)
        candidate_layout.addWidget(self._equip_card(
            candidate.get(EQUIP_SHAPE_ID) or candidate.get(EQUIP_SET_NAME, core_term), candidate.get(EQUIP_MAIN_STATS, ""),
            candidate.get(EQUIP_SUB_STATS, {}), candidate.get(EQUIP_SHAPE_ID), candidate.get(EQUIP_UID, ""), weights,
            (candidate_score, self._calc_grade(candidate_score, 15 if item_kind == "tape" else self._shape_areas.get(candidate.get(EQUIP_SHAPE_ID, ""), 3))),
            candidate.get(EQUIP_QUALITY, "Gold"),
            is_duplicate_drive=item_kind == "drive" and bool(candidate.get("is_duplicate_drive")),
            main_weights=main_weights, replacement_callback=apply_replacement,
            replacement_text="替换", card_variant="inventory",
            item_icon_path=_replacement_item_icon(asset_catalog, item_kind, candidate),
        ))
        if rank_by_damage:
            margin = QLabel(f"直伤收益：{candidate_margin:+.2f}%")
            margin.setStyleSheet(themed_style("color:#ffaa00;font-weight:700;font-size:12px"))
            candidate_layout.addWidget(margin)
        used_by = tuple(candidate.get("_used_by") or ())
        if used_by:
            user_label = QLabel(f"使用者：{', '.join(used_by)}")
            user_label.setStyleSheet(themed_style("color:#ff9800;font-size:12px"))
            candidate_layout.addWidget(user_label)
        content_layout.addWidget(candidate_card)
    content_layout.addStretch()
    scroll.setWidget(content)
    candidates_layout.addWidget(scroll)
    layout.addWidget(candidates_group, 1)
    close = QPushButton("关闭")
    close.clicked.connect(dialog.accept)
    layout.addWidget(close)
    dialog.exec()

def _render_equip_batch(self, token, batch_size=None):
    if token is not getattr(self,"_equip_render_token",None):
        return

    queue=getattr(self,"_equip_render_queue",[])
    index=getattr(self,"_equip_render_index",0)
    size=batch_size or EQUIPMENT_RENDER_BATCH_SIZE
    end=min(index+size,len(queue))
    for role_name,rd in queue[index:end]:
        _render_equip_role(self,role_name,rd)
    self._equip_render_index=end
    if end < len(queue):
        QTimer.singleShot(0, lambda: _render_equip_batch(self, token))
    elif not getattr(self,"_equip_render_stretch_added",False):
        self.equip_content_layout.addStretch()
        self._equip_render_stretch_added=True

def _render_equip_role(self, role_name, rd):
    role_cfg=self.roles_db.get(role_name,{})
    wts=role_cfg.get("weights",{})
    main_wts=role_cfg.get("main_weights")
    is_sqlite_plan="_sqlite_plan_id" in rd

    total_score=0.0
    tape_data=rd.get(ROLE_EQUIPPED_TAPE)
    if is_sqlite_plan:
        total_score=float(rd.get(ROLE_TOTAL_SCORE,0.0) or 0.0)
        total_grade=self._calc_grade(total_score,ALLOCATION_TOTAL_SCORE_AREA)
    elif ROLE_TOTAL_SCORE in rd and rd.get(ROLE_TOTAL_GRADE):
        total_score=float(rd.get(ROLE_TOTAL_SCORE,0.0) or 0.0)
        total_grade=str(rd.get(ROLE_TOTAL_GRADE) or "D")
    else:
        if tape_data:
            t_q=tape_data.get(EQUIP_QUALITY,"Gold")
            t_s=self._score_tape_dict(tape_data.get(EQUIP_MAIN_STATS,""),tape_data.get(EQUIP_SUB_STATS,{}),wts,t_q,main_wts)
            total_score+=t_s
        for d in rd.get(ROLE_EQUIPPED_DRIVES,[]):
            d_q=d.get(EQUIP_QUALITY,"Gold")
            d_s=self._score_drive_dict(d.get(EQUIP_SUB_STATS,{}),d.get(EQUIP_SHAPE_ID,""),wts,d_q)
            total_score+=d_s
        total_grade=self._calc_grade(total_score,ALLOCATION_TOTAL_SCORE_AREA)
    gc=GRADE_COLORS.get(total_grade,"#58a6ff")
    gbg=theme_rgba(gc, 0.10)

    grp=QGroupBox(""); grp.setStyleSheet(themed_style("QGroupBox{background:#0d1117;border:1px solid #30363d;border-radius:10px;margin-top:12px;padding:18px}"))
    gl=QVBoxLayout(grp); gl.setSpacing(10)
    role_hdr=QHBoxLayout(); role_hdr.setSpacing(8)
    rnl=QLabel(role_name)
    rnl.setStyleSheet(f"font-size:15px;font-weight:800;color:{theme_color('#4dd0e1')};border:1px solid {theme_color('#4dd0e1')};border-radius:7px;padding:4px 14px;background:{theme_rgba('#4dd0e1', 0.10)}")
    role_hdr.addWidget(rnl)
    last_diff=rd.get(ROLE_LAST_DIFF,{}) or {}
    if last_diff.get(DIFF_CHANGED):
        diff_btn=QPushButton("变动")
        diff_btn.setFixedSize(76,32)
        diff_btn.setStyleSheet(themed_style("QPushButton{background:#1f6feb;color:#ffffff;border:1px solid #58a6ff;border-radius:6px;font-size:13px;font-weight:700;padding:0;min-width:76px;min-height:32px}QPushButton:hover{background:#388bfd}"))
        diff_btn.clicked.connect(lambda _=False,rn=role_name,d=last_diff: self._show_saved_plan_diff_dialog(rn,d))
        role_hdr.addWidget(diff_btn)
    _sm=rd.get("strategy_mode","")
    if _sm:
        _ml={"role_priority":"角色优先","drive_priority":"驱动优先","global_optimal":"全局最优","update_mode":"增量更新"}.get(_sm,_sm)
        sml=QLabel(_ml); sml.setStyleSheet(themed_style("font-size:12px;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 8px"))
        role_hdr.addWidget(sml)
    role_hdr.addStretch()
    # Score
    sf=QFrame()
    sf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
    slb=QHBoxLayout(sf); slb.setSpacing(6); slb.setContentsMargins(4,0,4,0)
    sv=QLabel(f"{total_score:.1f}"); sv.setStyleSheet(f"font-size:14px;font-weight:800;color:{gc};border:none")
    slb.addWidget(QLabel("评分")); slb.addWidget(sv); role_hdr.addWidget(sf)
    # Grade
    gf=QFrame()
    gf.setStyleSheet(f"QFrame{{background:{gbg};border:1px solid {gc};border-radius:7px;padding:4px 12px}}")
    glb=QHBoxLayout(gf); glb.setSpacing(6); glb.setContentsMargins(4,0,4,0)
    gv=QLabel(total_grade); gv.setStyleSheet(f"font-size:14px;font-weight:800;color:{gc};border:none")
    glb.addWidget(QLabel("评级")); glb.addWidget(gv); role_hdr.addWidget(gf)
    del_btn=QPushButton("删除"); del_btn.setObjectName("btnDanger")
    del_btn.setFixedSize(64,32)
    del_btn.clicked.connect(lambda _=False, rn=role_name: self._delete_role_equipment(rn))
    role_hdr.addWidget(del_btn)
    import_btn = QPushButton("装配")
    import_btn.setObjectName("btnPrimary")
    import_btn.clicked.connect(lambda _, rn=role_name: self._preview_assemble_role(rn))
    role_hdr.addWidget(import_btn)
    gl.addLayout(role_hdr); gl.addSpacing(6)

    bp=rd.get(ROLE_BLUEPRINT_LAYOUT,[])
    drives=rd.get(ROLE_EQUIPPED_DRIVES,[])
    if bp:
        gl.addWidget(self._section_label("拼图图纸:"))
        compare_with_saved=bool(last_diff.get(DIFF_CHANGED))
        bp_row=QHBoxLayout(); bp_row.setSpacing(18)
        bp_row.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        bp_row.addWidget(PuzzleBoardWidget(bp),0,Qt.AlignTop)
        bp_row.addWidget(
            self._role_bonus_summary_panel(
                role_name,
                tape_data,
                drives,
                compare_with_saved=compare_with_saved,
                priority_stats=self._role_stat_priority_stats(role_name),
                role_diff=last_diff,
            ),
            1 if compare_with_saved else 0,
            Qt.AlignTop,
        )
        gl.addLayout(bp_row)
    if tape_data:
        t_q=tape_data.get(EQUIP_QUALITY,"Gold")
        if EQUIP_SCORE in tape_data and tape_data.get(EQUIP_GRADE):
            t_s=float(tape_data.get(EQUIP_SCORE,0.0) or 0.0)
            t_g=str(tape_data.get(EQUIP_GRADE) or "D")
        else:
            t_s=self._score_tape_dict(tape_data.get(EQUIP_MAIN_STATS,""),tape_data.get(EQUIP_SUB_STATS,{}),wts,t_q,main_wts)
            t_g=self._calc_grade(t_s,15)
        gl.addWidget(self._section_label("卡带:"))
        tape_changed=bool(tape_data.get(EQUIP_IS_CHANGED))
        tape_uid=tape_data.get(EQUIP_UID,"")
        gl.addWidget(self._equip_card(tape_data.get(EQUIP_SET_NAME,""),tape_data.get(EQUIP_MAIN_STATS,""),tape_data.get(EQUIP_SUB_STATS,{}),None,tape_uid,wts,(t_s,t_g),t_q,is_new=bool(tape_data.get(EQUIP_IS_NEW)) and not tape_changed,is_changed=tape_changed,is_discarded=bool(tape_data.get("discarded")),main_weights=main_wts,replacement_callback=lambda rn=role_name, item_uid=tape_uid: self._optimize_saved_equipment(rn,"tape",item_uid),card_variant="inventory"))
    if drives:
        gl.addWidget(self._section_label(f"驱动 ({len(drives)}个):"))
        for d in drives:
            d_q=d.get(EQUIP_QUALITY,"Gold")
            if EQUIP_SCORE in d and d.get(EQUIP_GRADE):
                d_s=float(d.get(EQUIP_SCORE,0.0) or 0.0)
                d_g=str(d.get(EQUIP_GRADE) or "D")
            else:
                d_s=self._score_drive_dict(d.get(EQUIP_SUB_STATS,{}),d.get(EQUIP_SHAPE_ID,""),wts,d_q)
                d_g=self._calc_grade(d_s,self._shape_areas.get(d.get(EQUIP_SHAPE_ID,""),3))
            drive_changed=bool(d.get(EQUIP_IS_CHANGED))
            drive_uid=d.get(EQUIP_UID,"")
            gl.addWidget(self._equip_card(d.get(EQUIP_SHAPE_ID,""),"",d.get(EQUIP_SUB_STATS,{}),d.get(EQUIP_SHAPE_ID,""),drive_uid,wts,(d_s,d_g),d_q,is_new=bool(d.get(EQUIP_IS_NEW)) and not drive_changed,is_changed=drive_changed,is_discarded=bool(d.get("discarded")),is_duplicate_drive=bool(d.get("is_duplicate_drive")),replacement_callback=lambda rn=role_name, item_uid=drive_uid: self._optimize_saved_equipment(rn,"drive",item_uid),card_variant="inventory"))
    self.equip_content_layout.addWidget(grp)

def _saved_plan_diff_text(self, role_name, diff):
    removed=diff.get(DIFF_REMOVED,[]) or []
    added=diff.get(DIFF_ADDED,[]) or []
    if not removed and not added:
        return "本次保存与上一套方案没有装备变动。"
    lines=[f"{role_name} 配装变动："]
    if removed:
        lines.append("\n卸下：")
        lines.extend(f"- {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in removed)
    if added:
        lines.append("\n换上：")
        lines.extend(f"+ {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in added)
    return "\n".join(lines)

def _show_saved_plan_diff_dialog(self, role_name, diff):
    if hasattr(self, "_build_plan_diff_dialog"):
        self._build_plan_diff_dialog(role_name, diff).exec()
        return
    QMessageBox.information(self,"配装变动",self._saved_plan_diff_text(role_name,diff))

def _clear_all_equipment(self):
    with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
        plans=dao.list_active_loadout_plans_by_role()
    if not plans:
        QMessageBox.information(self,"清空配装","当前没有已保存的配装。")
        return
    ret=QMessageBox.question(
        self,
        "清空配装",
        "确定要从当前配装页移除所有已保存方案吗？\n方案历史和任务记录会保留，但这些方案不再参与装配。",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret!=QMessageBox.Yes:
        return
    with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
        for plan in plans.values():
            dao.deactivate_loadout_plan(plan["plan_id"])
    self._refresh_equip()
    logger.success("已清空所有角色配装")

def _delete_role_equipment(self, role_name: str):
    with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
        plan=dao.get_active_loadout_plan_for_role(role_name)
    if plan is None:
        self._refresh_equip()
        return
    ret=QMessageBox.question(
        self,
        "删除角色配装",
        f"确定要从当前配装页移除 [{role_name}] 的已保存方案吗？\n方案历史会保留，但该方案不再参与装配。",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret!=QMessageBox.Yes:
        return
    with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
        dao.deactivate_loadout_plan(plan["plan_id"])
    self._refresh_equip()
    logger.success(f"已删除角色配装: {role_name}")


def _assembly_report_dialog(action_name: str, report, expected_role_count: int | None = None):
    """Build a completion/warning dialog from a game assembly execution report."""
    role_count = len(getattr(report, "role_reports", []) or [])
    action_count = getattr(report, "executed_actions", 0)
    missing = list(getattr(report, "missing_roles", []) or [])
    skipped = list(getattr(report, "skipped_roles", []) or [])
    duplicates = list(getattr(report, "duplicate_roles", []) or [])
    unrecognized = list(getattr(report, "unrecognized_roles", []) or [])
    verification_failures = list(getattr(report, "verification_failures", []) or [])

    incomplete = bool(missing or skipped or duplicates or unrecognized or verification_failures)
    if expected_role_count is not None and role_count < expected_role_count:
        incomplete = True
    if role_count == 0:
        incomplete = True

    title = f"{action_name}未完成" if incomplete else f"{action_name}完成"
    lines = [f"已装配 {role_count} 个角色，执行 {action_count} 个动作。"]
    if expected_role_count is not None and role_count < expected_role_count:
        lines.append(f"预计装配 {expected_role_count} 个角色，还有 {expected_role_count - role_count} 个未完成。")
    if missing:
        lines.append("未找到角色：" + "、".join(str(role) for role in missing))
    if skipped:
        lines.append("跳过角色：" + "、".join(str(role) for role in skipped))
    if duplicates:
        lines.append(f"重复识别角色槽位：{len(duplicates)} 个。")
    if unrecognized:
        lines.append(f"未识别角色槽位：{len(unrecognized)} 个。")
        for entry in unrecognized:
            if not isinstance(entry, dict):
                lines.append(f"- {entry}")
                continue
            if entry.get("roster_index") is not None:
                position = f"第 {int(entry['roster_index']) + 1} 个角色"
            elif entry.get("page_index") is not None and entry.get("slot_index") is not None:
                position = f"第 {int(entry['page_index']) + 1} 页第 {int(entry['slot_index']) + 1} 个角色"
            else:
                position = "未知位置"
            raw_text = str(entry.get("raw_text") or "").strip() or "未读取到文字"
            lines.append(f"- {position}（OCR：{raw_text}）")
    if verification_failures:
        lines.append(f"图纸截图校验失败：{len(verification_failures)} 个。")
        for failure in verification_failures:
            if not isinstance(failure, dict):
                continue
            role_name = str(failure.get("role_name") or "未知角色")
            block_ids = [
                str(item.get("block_id"))
                for item in (failure.get("missing_blocks") or [])
                if isinstance(item, dict) and item.get("block_id") is not None
            ]
            if block_ids:
                lines.append(f"- {role_name}：未通过校验的驱动块 #{'、#'.join(block_ids)}")
    if incomplete:
        lines.append("请检查角色识别结果后重新执行。")
    return title, "\n".join(lines), not incomplete


def _return_to_equipment_after_assembly(self) -> None:
    """Restore the calculator window and return to the equipment page."""
    show_normal = getattr(self, "showNormal", None)
    if callable(show_normal):
        show_normal()
    go_to_page = getattr(self, "_go", None)
    if callable(go_to_page):
        go_to_page("equipment")
    raise_window = getattr(self, "raise_", None)
    if callable(raise_window):
        raise_window()
    activate_window = getattr(self, "activateWindow", None)
    if callable(activate_window):
        activate_window()


def _prompt_protagonist_alias_if_needed(self, role_names) -> dict[str, str]:
    roles = {str(role).strip() for role in (role_names or []) if str(role).strip()}
    if "主角" not in roles:
        return {}
    default_name = str(getattr(self, "_drive_assembly_protagonist_name", "") or "").strip()
    player_name, ok = QInputDialog.getText(
        self,
        "主角名称",
        "请输入游戏中主角显示的名字：",
        QLineEdit.Normal,
        default_name,
    )
    if not ok:
        return {}
    player_name = str(player_name).strip()
    if not player_name:
        QMessageBox.warning(self, "主角名称", "需要输入主角在游戏中显示的名字。")
        return {}
    self._drive_assembly_protagonist_name = player_name
    return {"主角": player_name}


def _is_equipment_plugin_unavailable_error(error: object) -> bool:
    """识别核心已启动但游戏内装备插件桥接不可用的不可重试错误。"""

    return "EQUIPMENT_PLUGIN_UNAVAILABLE" in str(error)


def _run_nte_core_equipment_apply(
    self,
    role_names: list[str],
    *,
    identity_overrides: dict[str, dict] | None = None,
    job_id: int | None = None,
) -> dict:
    sync_service = getattr(self, "_inventory_sync_service", None)
    if sync_service is None:
        raise RuntimeError("背包同步服务尚未启动，请先在首页启动后台同步")

    identity_overrides = identity_overrides or {}
    applied: list[dict] = []
    with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
        apply_service = EquipmentApplyService(user_dao, sync_service)
        if job_id is not None:
            job = user_dao.get_equipment_apply_job(job_id)
            if job is None:
                raise RuntimeError(f"装配任务 {job_id} 不存在")
            user_dao.reset_failed_equipment_apply_job_items(job_id)
            prepared = [
                {
                    "job_item_id": row["job_item_id"],
                    "role_name": row["role_name"],
                    "character_id": row["character_id"],
                    "character_uid": row["character_uid"],
                    "plan_id": row["plan_id"],
                }
                for row in job["items"] if row["status"] in {"pending", "running", "failed"}
            ]
            if not prepared:
                return {"job_id": job_id, "applied": [], "completed": job["status"] == "completed"}
        else:
            initial_snapshot_id = user_dao.current_inventory_snapshot_id()
            if initial_snapshot_id is None:
                raise RuntimeError("用户数据库中还没有稳定背包快照")

            # 必须在第一条装配指令前缓存全部角色 UID。后续角色可能因装备被前面的
            # 方案移走而暂时全身为空，此时再从当前快照解析会失败。
            prepared: list[dict] = []
            identity_requests: list[dict] = []
            for role_name in role_names:
                plan = user_dao.get_active_loadout_plan_for_role(role_name)
                if plan is None:
                    raise RuntimeError(
                        f"装配前检查 [{role_name}] 失败，尚未发送任何装配指令："
                        "没有来自官方背包快照的已保存方案，请重新计算并保存。"
                    )
                source_snapshot_id = plan.get("source_snapshot_id")
                source_summary = (
                    user_dao.inventory_snapshot_summary(int(source_snapshot_id))
                    if source_snapshot_id is not None else None
                )
                if source_summary is None or source_summary.get("source") != "nte_core":
                    raise RuntimeError(
                        f"装配前检查 [{role_name}] 失败，视觉扫描库存没有本地组件可用的原生 UID。"
                        "请改用自动装配；极速装配仅支持抓包稳定快照。"
                    )
                override = identity_overrides.get(role_name)
                try:
                    character_id = int(override["character_id"]) if override else int(plan["character_id"])
                    if character_id != int(plan["character_id"]):
                        raise RuntimeError("手动选择的角色 ID 与该 SQLite 方案不匹配")
                    character_uid = apply_service.resolve_character_uid(
                        character_id, initial_snapshot_id,
                        explicit_uid=override.get("character_uid") if override else None,
                    )
                except Exception as exc:
                    identity_requests.append(
                        {
                            "role_name": role_name,
                            "candidate_character_ids": [int(plan["character_id"])],
                            "reason": str(exc),
                        }
                    )
                    continue
                prepared.append({
                    "role_name": role_name, "character_id": character_id,
                    "character_uid": character_uid, "plan_id": plan["plan_id"],
                    "module_count": sum(1 for row in plan["assignments"] if row["kind"] == "module"),
                })
            if identity_requests:
                return {"identity_requests": identity_requests}
            job_id = user_dao.create_equipment_apply_job(initial_snapshot_id, prepared)
            for entry, prepared_role in zip(user_dao.get_equipment_apply_job(job_id)["items"], prepared):
                prepared_role["job_item_id"] = entry["job_item_id"]

        for prepared_role in prepared:
            role_name = prepared_role["role_name"]
            user_dao.mark_equipment_apply_job_item(prepared_role["job_item_id"], status="running")
            try:
                result = apply_service.apply_plan(
                    prepared_role["plan_id"],
                    character_uid=prepared_role["character_uid"],
                    timeout=30.0,
                )
                user_dao.mark_equipment_apply_job_item(
                    prepared_role["job_item_id"], status="succeeded",
                    before_snapshot_id=result.before_snapshot_id,
                    after_snapshot_id=result.after_snapshot_id,
                )
                applied.append(
                    {
                        "role_name": role_name,
                        "character_id": prepared_role["character_id"],
                        "plan_id": prepared_role["plan_id"],
                        "module_count": prepared_role.get("module_count"),
                        "snapshot_id": result.after_snapshot_id,
                        "already_applied": result.already_applied,
                    }
                )
            except Exception as exc:
                user_dao.mark_equipment_apply_job_item(prepared_role["job_item_id"], status="failed", error=str(exc))
                return {"job_id": job_id, "applied": applied, "failed_role": role_name, "error": str(exc), "completed": False}
        completed = user_dao.complete_equipment_apply_job_if_done(job_id)
    return {"job_id": job_id, "applied": applied, "completed": completed}


def _prompt_character_identity_requests(self, requests: list[dict]) -> dict[str, dict] | None:
    overrides: dict[str, dict] = {}
    for request in requests:
        role_name = request["role_name"]
        choices = [str(value) for value in request.get("candidate_character_ids") or []]
        if not choices:
            QMessageBox.warning(self, "角色实例", f"[{role_name}] 没有可选的官方角色 ID。")
            return None
        character_id, ok = QInputDialog.getItem(
            self, "选择角色实例", f"[{role_name}] 无法自动确定身份。\n原因：{request['reason']}\n\n请选择官方角色 ID：", choices, 0, False,
        )
        if not ok:
            return None
        uid_text, ok = QInputDialog.getText(
            self, "输入角色实例 UID", f"请输入 [{role_name}] 的实例 UID（slot,serial）：", QLineEdit.Normal,
        )
        if not ok:
            return None
        try:
            slot_text, serial_text = [value.strip() for value in str(uid_text).split(",", 1)]
            uid = {"slot": int(slot_text), "serial": int(serial_text)}
            with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
                user_dao.upsert_character_instance_mapping(int(character_id), uid, source="manual")
        except Exception as exc:
            QMessageBox.warning(self, "角色实例", f"实例 UID 无效或无法保存：{exc}")
            return None
        overrides[role_name] = {"character_id": int(character_id), "character_uid": uid}
    return overrides


def _start_nte_core_equipment_apply(self, role_names: list[str], *, identity_overrides: dict[str, dict] | None = None, job_id: int | None = None) -> None:
    current_worker = getattr(self, "_equipment_apply_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "正在装配", "已有装配任务正在执行，请等待结果验证完成。")
        return

    worker = WorkerThread(
        target=lambda: _run_nte_core_equipment_apply(self, role_names, identity_overrides=identity_overrides, job_id=job_id),
        parent=self,
    )
    self._equipment_apply_worker = worker

    def on_result(report: dict) -> None:
        requests = report.get("identity_requests") or []
        if requests:
            overrides = _prompt_character_identity_requests(self, requests)
            if overrides is not None:
                _start_nte_core_equipment_apply(self, role_names, identity_overrides=overrides)
            return
        applied = report.get("applied") or []
        details = "\n".join(
            f"• {row['role_name']}"
            + (f"：{row['module_count']} 个驱动 + 1 个核心" if row.get("module_count") is not None else "：已确认")
            + ("（原本已装好）" if row.get("already_applied") else "")
            for row in applied
        )
        changed_count = sum(not row.get("already_applied") for row in applied)
        unchanged_count = len(applied) - changed_count
        summary = f"已确认 {len(applied)} 个角色的配装"
        if unchanged_count:
            summary += f"（实际装配 {changed_count} 个，原本已装好 {unchanged_count} 个）"
        if report.get("failed_role"):
            error_message = str(report.get("error") or "未知错误")
            if _is_equipment_plugin_unavailable_error(error_message):
                QMessageBox.warning(
                    self,
                    "装备插件不可用",
                    f"任务 #{report.get('job_id')} 在 [{report['failed_role']}] 停止。\n"
                    "本地核心组件已连接，但未能连接游戏内装备插件（命名管道不可用或超时）。\n\n"
                    "请先确认：\n"
                    "1. 当前运行的 HTGame.exe 已加载与本地核心组件版本匹配的装备插件；\n"
                    "2. 游戏保持登录，随后从首页重新启动背包同步并等待“后台监听”；\n"
                    "3. 完成上述检查后，再点击右上角“极速装配”重新执行。\n\n"
                    f"此前已确认 {len(applied)} 个角色；任务日志已保存。此次不会立即重试。",
                )
                return
            retry = QMessageBox.question(
                self, "装配暂停",
                f"任务 #{report.get('job_id')} 在 [{report['failed_role']}] 停止。\n{error_message}\n\n"
                f"此前已确认 {len(applied)} 个角色；任务日志已保存。是否重试失败角色并继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if retry == QMessageBox.Yes:
                _start_nte_core_equipment_apply(self, [], job_id=report["job_id"])
            return
        QMessageBox.information(self, "装配完成", f"{summary}。\n任务 #{report.get('job_id')} 已保存日志。\n\n{details}")
        refresh = getattr(self, "_refresh_equip", None)
        if callable(refresh):
            refresh()

    def on_error(message: str) -> None:
        QMessageBox.critical(
            self,
            "装配失败",
            f"本地组件未能完成装配：\n{message}\n\n"
            "请确认游戏已登录、插件已加载，且首页背包同步处于“后台监听”。",
        )

    worker.result_ready.connect(on_result)
    worker.error.connect(on_error)
    worker.start()


def _preview_nte_core_assemble_role(self, role_name: str, *, confirmed: bool = False) -> None:
    """确认后通过装备插件极速装配一个已保存角色方案。"""

    try:
        with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
            plan = user_dao.get_active_loadout_plan_for_role(role_name)
            source_snapshot_id = plan.get("source_snapshot_id") if plan else None
            source = (
                user_dao.inventory_snapshot_summary(int(source_snapshot_id)).get("source")
                if source_snapshot_id is not None
                and user_dao.inventory_snapshot_summary(int(source_snapshot_id)) is not None
                else None
            )
    except Exception as exc:
        QMessageBox.warning(self, "极速装配", f"无法读取已保存方案：{exc}")
        return
    if source == "gamepad":
        QMessageBox.information(
            self,
            "切换自动装配",
            "未找到抓包稳定快照。视觉扫描库存不包含本地组件所需的原生 UID，"
            "将改用逐步自动装配。",
        )
        _preview_automatic_assemble_role(self, role_name, confirmed=confirmed)
        return

    if confirmed:
        _start_nte_core_equipment_apply(self, [role_name])
        return
    ret = QMessageBox.question(
        self,
        "极速装配",
        f"将通过游戏内装备插件把 [{role_name}] 的已保存方案直接装入游戏。\n\n"
        "若当前已经是目标配装会立即完成，否则发送指令并等待稳定背包快照确认；"
        "不需要切换到游戏配装页面。是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        _start_nte_core_equipment_apply(self, [role_name])


def _preview_nte_core_assemble_all_roles(
    self, *, confirmed: bool = False, role_names: list[str] | None = None,
) -> None:
    requested_roles = tuple(dict.fromkeys(str(name) for name in (role_names or ())))
    try:
        with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
            plans_by_role = user_dao.list_active_loadout_plans_by_role()
            if requested_roles:
                missing = [name for name in requested_roles if name not in plans_by_role]
                if missing:
                    QMessageBox.information(
                        self, "极速装配", f"以下角色尚未保存当前方案：{'、'.join(missing)}",
                    )
                    return
                plans_by_role = {name: plans_by_role[name] for name in requested_roles}
            nte_roles = []
            visual_roles = []
            for role_name, plan in plans_by_role.items():
                snapshot_id = plan.get("source_snapshot_id")
                summary = user_dao.inventory_snapshot_summary(int(snapshot_id)) if snapshot_id is not None else None
                if summary and summary.get("source") == "nte_core":
                    nte_roles.append(role_name)
                elif summary and summary.get("source") == "gamepad":
                    visual_roles.append(role_name)
    except Exception as exc:
        QMessageBox.warning(self, "极速装配", f"无法读取官方 SQLite 方案：{exc}")
        return
    if nte_roles:
        role_names = list(nte_roles) if requested_roles else sorted(nte_roles)
    elif visual_roles:
        QMessageBox.information(
            self,
            "切换自动装配",
            "未找到抓包稳定快照。视觉扫描库存不包含本地组件所需的原生 UID，"
            "将改用逐步自动装配。",
        )
        _preview_automatic_assemble_all_roles(
            self, role_names=list(requested_roles) if requested_roles else None,
        )
        return
    else:
        QMessageBox.information(self, "极速装配", "当前没有来自官方背包快照的已保存方案。请先重新计算并保存。")
        return
    if confirmed:
        _start_nte_core_equipment_apply(self, role_names)
        return
    ret = QMessageBox.question(
        self,
        "极速装配",
        f"将依次向本地组件发送 {len(role_names)} 个角色的装配指令，"
        "已经正确装配的角色会直接跳过，其余角色在稳定背包快照确认后再处理下一个。"
        "\n\n是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        _start_nte_core_equipment_apply(self, role_names)


def _preview_fast_assemble_all_roles(self, role_names: list[str] | None = None) -> None:
    """从配装页右上角启动全部角色的极速装配。"""

    _preview_nte_core_assemble_all_roles(self, role_names=role_names)


def _sqlite_automatic_assembly_state(role_names: list[str]) -> dict[str, dict]:
    """从 SQLite 已保存方案构建自动装配动作所需的只读投影。"""

    with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao, StaticGameDataDao() as static_dao:
        states: dict[str, dict] = {}
        for role_name in role_names:
            plan = user_dao.get_active_loadout_plan_for_role(role_name)
            if plan is None:
                raise RuntimeError(f"[{role_name}] 没有来自官方背包快照的已保存方案")
            states[role_name] = _sqlite_plan_display_state(plan, user_dao, static_dao)
    return states


def _start_automatic_equipment_assembly(self, role_names: list[str]) -> None:
    """在工作线程中执行逐步游戏界面自动装配。"""

    current_worker = getattr(self, "_automatic_equipment_apply_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "自动装配", "已有自动装配任务正在执行，请等待它结束。")
        return
    try:
        state = _sqlite_automatic_assembly_state(role_names)
    except Exception as exc:
        QMessageBox.warning(self, "自动装配", f"无法读取官方 SQLite 方案：{exc}")
        return

    aliases = _prompt_protagonist_alias_if_needed(self, role_names)
    if "主角" in role_names and not aliases:
        return
    QMessageBox.information(
        self,
        "自动装配准备",
        "将模拟游戏内操作逐步装配。请在 3 秒内切换到游戏的角色详情页，"
        "并保持游戏窗口可见；执行期间可按 F12 停止。",
    )
    show_minimized = getattr(self, "showMinimized", None)
    if callable(show_minimized):
        show_minimized()

    def run() -> object:
        if len(role_names) == 1:
            return execute_selected_role_from_current_game_page(
                state, role_names[0], role_name_aliases=aliases,
            )
        return execute_all_roles_from_current_game_page(state, role_name_aliases=aliases)

    worker = WorkerThread(target=run, parent=self)
    self._automatic_equipment_apply_worker = worker

    def on_result(report: object) -> None:
        _return_to_equipment_after_assembly(self)
        title, message, completed = _assembly_report_dialog("自动装配", report, len(role_names))
        (QMessageBox.information if completed else QMessageBox.warning)(self, title, message)
        refresh = getattr(self, "_refresh_equip", None)
        if callable(refresh):
            refresh()

    def on_error(message: str) -> None:
        _return_to_equipment_after_assembly(self)
        QMessageBox.critical(self, "自动装配失败", f"自动装配未能完成：\n{message}")

    worker.result_ready.connect(on_result)
    worker.error.connect(on_error)
    worker.start()


def _confirm_automatic_assembly_duplicate_warning(self) -> bool:
    """Warn once that UI automation cannot resolve repeated drive placement."""
    preferences = getattr(self, "_ui_preferences", None)
    if isinstance(preferences, dict) and preferences.get("skip_automatic_assembly_duplicate_warning"):
        return True

    dialog = QMessageBox(self)
    dialog.setWindowTitle("自动装配提示")
    dialog.setIcon(QMessageBox.Warning)
    dialog.setText("自动装配无法完美处理重复驱动情况。")
    dialog.setInformativeText("运行结束后，请自行填补因重复驱动产生的空缺。")
    dialog.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
    dialog.setDefaultButton(QMessageBox.Cancel)
    dont_remind = QCheckBox("不再提醒")
    dialog.setCheckBox(dont_remind)
    confirm_button = dialog.button(QMessageBox.Ok)
    dialog.exec()
    if dialog.clickedButton() is not confirm_button:
        return False
    if dont_remind.isChecked():
        if not isinstance(preferences, dict):
            preferences = {}
            self._ui_preferences = preferences
        preferences["skip_automatic_assembly_duplicate_warning"] = True
        saver = getattr(self, "_save_ui_preferences", None)
        if callable(saver):
            try:
                saver()
            except Exception as exc:
                logger.warning(f"保存自动装配提示偏好失败: {exc}")
    return True


def _preview_automatic_assemble_role(self, role_name: str, *, confirmed: bool = False) -> None:
    """确认后通过游戏界面自动化装配一个角色。"""

    if not confirmed:
        ret = QMessageBox.question(
            self,
            "自动装配",
            f"将模拟游戏内操作逐步装配 [{role_name}]。\n\n"
            "不需要装备插件，但需切换到游戏角色详情页，耗时更长；执行期间可按 F12 停止。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
    if not _confirm_automatic_assembly_duplicate_warning(self):
        return
    _start_automatic_equipment_assembly(self, [role_name])


def _preview_automatic_assemble_all_roles(
    self, role_names: list[str] | None = None,
) -> None:
    """确认后通过游戏界面自动化装配全部已保存角色。"""

    requested_roles = tuple(dict.fromkeys(str(name) for name in (role_names or ())))
    try:
        with UserDataDao(runtime.USER_DATABASE_PATH) as user_dao:
            plans_by_role = user_dao.list_active_loadout_plans_by_role()
    except Exception as exc:
        QMessageBox.warning(self, "自动装配", f"无法读取官方 SQLite 方案：{exc}")
        return
    if requested_roles:
        missing = [name for name in requested_roles if name not in plans_by_role]
        if missing:
            QMessageBox.information(
                self, "自动装配", f"以下角色尚未保存当前方案：{'、'.join(missing)}",
            )
            return
        role_names = list(requested_roles)
    else:
        role_names = sorted(plans_by_role)
    if not role_names:
        QMessageBox.information(self, "自动装配", "当前没有来自官方背包快照的已保存方案。请先重新计算并保存。")
        return
    ret = QMessageBox.question(
        self,
        "自动装配",
        f"将模拟游戏内操作，依次装配 {len(role_names)} 个角色。\n\n"
        "无需装备插件，但需切换到游戏角色详情页，耗时更长；执行期间可按 F12 停止。是否继续？",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret == QMessageBox.Yes:
        if _confirm_automatic_assembly_duplicate_warning(self):
            _start_automatic_equipment_assembly(self, role_names)


def _select_single_role_assembly_mode(self, role_name: str) -> str | None:
    """让用户为一个角色显式选择极速或自动装配。"""

    dialog = QMessageBox(self)
    dialog.setWindowTitle("选择装配方式")
    dialog.setIcon(QMessageBox.Question)
    # QMessageBox 会根据标签内容重新收缩；同时设置标签最小宽度和初始尺寸，
    # 确保两种装配方式的说明不会挤在窄弹窗里。
    dialog.setMinimumSize(720, 400)
    dialog.setStyleSheet(
        "QLabel#qt_msgbox_label,QLabel#qt_msgbox_informativelabel{min-width:620px;}"
    )
    dialog.setText(f"为 [{role_name}] 选择装配方式")
    dialog.setInformativeText(
        "极速装配：通过游戏内装备插件直接写入方案，速度快，无需打开配装页。\n\n"
        "自动装配：模拟游戏内操作逐步完成，无需装备插件，但需停在角色详情页且耗时更长。"
    )
    fast_button = dialog.addButton("极速装配", QMessageBox.ActionRole)
    automatic_button = dialog.addButton("自动装配", QMessageBox.ActionRole)
    dialog.addButton(QMessageBox.Cancel)
    dialog.resize(720, 400)
    dialog.exec()
    if dialog.clickedButton() is fast_button:
        return "fast"
    if dialog.clickedButton() is automatic_button:
        return "automatic"
    return None


def _preview_assemble_role(self, role_name: str) -> None:
    """为单个角色显示装配方式选择。"""

    mode = _select_single_role_assembly_mode(self, role_name)
    if mode == "fast":
        _preview_nte_core_assemble_role(self, role_name, confirmed=True)
    elif mode == "automatic":
        _preview_automatic_assemble_role(self, role_name, confirmed=True)
