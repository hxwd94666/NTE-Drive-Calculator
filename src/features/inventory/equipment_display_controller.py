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
    self.equip_scroll = scroll
    self.equip_content=QWidget(); self.equip_content_layout=QVBoxLayout(self.equip_content); scroll.setWidget(self.equip_content)
    scroll.verticalScrollBar().valueChanged.connect(
        lambda _value: _schedule_visible_equipment_render(self)
    )
    l.addWidget(scroll,1); return page


def _clear_equip_content(self):
    while self.equip_content_layout.count():
        it=self.equip_content_layout.takeAt(0)
        if it.widget(): it.widget().deleteLater()

def _load_sqlite_equipment_display_states(database_path):
    """Read display-only saved plans off the UI thread with shared snapshots.

    A multi-role allocation commonly binds every plan to one immutable
    snapshot.  Re-reading that snapshot and static catalogs per card was the
    principal loading bottleneck.
    """
    with UserDataDao(database_path) as user_dao, StaticGameDataDao() as static_dao:
        plans = user_dao.list_active_loadout_plans_by_role()
        snapshot_ids = {
            int(plan["source_snapshot_id"])
            for plan in plans.values()
            if plan.get("source_snapshot_id") is not None
        }
        referenced_uids_by_snapshot: dict[int, set[tuple[int, int]]] = {
            snapshot_id: set() for snapshot_id in snapshot_ids
        }
        for plan in plans.values():
            snapshot_id = int(plan["source_snapshot_id"])
            for assignment in plan.get("assignments") or ():
                raw = assignment.get("raw_assignment") or {}
                if is_virtual_equipment_assignment(raw):
                    continue
                try:
                    referenced_uids_by_snapshot[snapshot_id].add((
                        int(assignment["uid_serial"]),
                        int(assignment["uid_slot"]),
                    ))
                except (KeyError, TypeError, ValueError):
                    continue
        inventory_by_snapshot = {
            snapshot_id: {
                (row["uid_serial"], row["uid_slot"]): row
                for row in user_dao.list_inventory_items(
                    snapshot_id,
                    uids=referenced_uids_by_snapshot[snapshot_id],
                )
            }
            for snapshot_id in snapshot_ids
        }
        shape_cells = {
            shape["shape_id"]: shape.get("cells") or []
            for shape in static_dao.list_shapes()
        }
        suit_names = {
            str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"])
            for suit in static_dao.list_suits()
        }
        attribute_ids = {
            str(attribute["attribute_id"])
            for attribute in static_dao.list_equipment_attributes()
        }
        displays = {}
        for role_name, plan in plans.items():
            display = _sqlite_plan_display_state(
                plan,
                user_dao,
                static_dao,
                inventory_by_snapshot=inventory_by_snapshot,
                shape_cells=shape_cells,
                suit_names=suit_names,
                attribute_ids=attribute_ids,
            )
            displays[role_name] = display
        return displays


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
    self._equip_lazy_entries = []

    if not roles:
        ph=QLabel("暂无已保存的配装。请先执行分配并保存。"); ph.setStyleSheet(themed_style("color:#6e7681;padding:24px")); ph.setAlignment(Qt.AlignCenter); self.equip_content_layout.addWidget(ph)
        self.equip_content_layout.addStretch()
        return

    if not isinstance(self, QWidget):
        self._equip_render_queue = roles
        self._equip_render_index = 0
        self._equip_render_stretch_added = False
        _render_equip_batch(self, token, EQUIPMENT_INITIAL_RENDER_COUNT)
        return

    for role_name, rd in roles:
        slot = QWidget(self.equip_content)
        slot.setObjectName("equipmentRolePlaceholder")
        slot.setFixedHeight(EQUIPMENT_ROLE_PLACEHOLDER_HEIGHT)
        slot_layout = QVBoxLayout(slot)
        slot_layout.setContentsMargins(0, 0, 0, 0)
        placeholder = QLabel(f"{role_name} · 滚动到此处加载配装详情")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet(themed_style("color:#6e7681;font-size:12px"))
        slot_layout.addWidget(placeholder)
        self.equip_content_layout.addWidget(slot)
        self._equip_lazy_entries.append({
            "role_name": role_name,
            "state": rd,
            "slot": slot,
            "layout": slot_layout,
            "loaded": False,
        })
    self.equip_content_layout.addStretch()
    _schedule_visible_equipment_render(self, token)
    _start_equipment_graduation_load(self, token, [role_name for role_name, _rd in roles])


def _schedule_visible_equipment_render(self, token=None):
    current = token or getattr(self, "_equip_render_token", None)
    if current is None:
        return
    QTimer.singleShot(0, lambda: _render_visible_equipment_roles(self, current))


def _clear_layout_widgets(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


def _render_lazy_equipment_entry(self, entry):
    layout = entry["layout"]
    _clear_layout_widgets(layout)
    group = _render_equip_role(
        self, entry["role_name"], entry["state"], target_layout=layout,
    )
    entry["loaded"] = True
    entry["slot"].setFixedHeight(max(
        EQUIPMENT_ROLE_PLACEHOLDER_HEIGHT,
        group.sizeHint().height() + 8,
    ))


def _render_visible_equipment_roles(self, token):
    if token is not getattr(self, "_equip_render_token", None):
        return
    scroll = getattr(self, "equip_scroll", None)
    if scroll is None:
        return
    viewport_top = scroll.verticalScrollBar().value()
    viewport_height = max(1, scroll.viewport().height())
    viewport_bottom = viewport_top + viewport_height * (1 + EQUIPMENT_VIEWPORT_PREFETCH_COUNT)
    for entry in getattr(self, "_equip_lazy_entries", []):
        if entry["loaded"]:
            continue
        slot = entry["slot"]
        if slot.y() > viewport_bottom or slot.y() + slot.height() < viewport_top - viewport_height:
            continue
        _render_lazy_equipment_entry(self, entry)
    value = getattr(self, "_equip_restore_scroll_value", None)
    if value is not None:
        scroll.verticalScrollBar().setValue(int(value))
        self._equip_restore_scroll_value = None


def _start_equipment_graduation_load(self, token, role_names):
    """Load optional role-detail metrics after the initial card viewport is ready."""
    worker = WorkerThread(
        target=lambda: {
            role_name: _saved_plan_graduation_info(role_name)
            for role_name in role_names
        },
        parent=self,
    )
    self._equip_graduation_worker = worker

    def apply_results(results):
        if token is not getattr(self, "_equip_render_token", None):
            return
        for entry in getattr(self, "_equip_lazy_entries", []):
            info = results.get(entry["role_name"]) if isinstance(results, dict) else None
            entry["state"]["_graduation_info"] = info
            if entry["loaded"]:
                _render_lazy_equipment_entry(self, entry)

    worker.result_ready.connect(apply_results)
    worker.error.connect(lambda error: logger.debug("配装毕业率后台加载失败: {}", error))
    worker.start()


def _on_sqlite_equipment_display_loaded(self, token, eq):
    if token is not getattr(self, "_equip_load_token", None):
        return
    _clear_equip_content(self)
    _queue_equipment_render(self, eq if isinstance(eq, dict) else {})


def _on_sqlite_equipment_display_error(self, token, error):
    if token is not getattr(self, "_equip_load_token", None):
        return
    logger.error(f"刷新 SQLite 配装展示失败: {error}")
    QMessageBox.warning(
        self,
        "已保存方案兼容性错误",
        "无法按当前官方静态数据解释部分已保存方案。"
        "方案未被修改；请查看日志中的形状、套装或属性 ID 后再决定是否重新计算。\n\n"
        f"详细原因：{error}",
    )
    _clear_equip_content(self)
    _queue_equipment_render(self, {})


def _refresh_equip(self):
    scroll = getattr(self, "equip_scroll", None)
    if scroll is not None:
        self._equip_restore_scroll_value = scroll.verticalScrollBar().value()
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


def _sqlite_plan_display_state(
    plan,
    user_dao,
    static_dao,
    *,
    inventory_by_snapshot=None,
    shape_cells=None,
    suit_names=None,
    attribute_ids=None,
):
    """将活动 SQLite 方案转换为配装页展示模型；不读取旧 JSON。"""
    snapshot_id=int(plan["source_snapshot_id"])
    if inventory_by_snapshot is None:
        items={
            (row["uid_serial"],row["uid_slot"]): row
            for row in user_dao.list_inventory_items(snapshot_id)
        }
    else:
        items=inventory_by_snapshot.get(snapshot_id, {})
    if shape_cells is None:
        shape_cells={shape["shape_id"]: shape.get("cells") or [] for shape in static_dao.list_shapes()}
    if suit_names is None:
        suit_names={str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"]) for suit in static_dao.list_suits()}
    if attribute_ids is None:
        attribute_ids={
            str(attribute["attribute_id"])
            for attribute in static_dao.list_equipment_attributes()
        }
    payload=plan.get("payload") or {}
    last_diff=dict(payload.get("last_diff") or {})
    added_uids={str(uid) for uid in (last_diff.get(DIFF_ADDED_UIDS) or ()) if uid}
    changed_uids={str(uid) for uid in (payload.get("changed_uids") or ()) if uid}
    board=[["0" for _ in range(5)] for _ in range(5)]
    drives=[]
    tape=None
    for assignment in plan["assignments"]:
        raw=assignment.get("raw_assignment") or {}
        item=(
            virtual_equipment_inventory_item(raw)
            if is_virtual_equipment_assignment(raw)
            else items.get((assignment["uid_serial"],assignment["uid_slot"]))
        )
        if item is None:
            message=(
                f"方案 #{plan.get('plan_id')} 的装备 UID "
                f"({assignment.get('uid_slot')}, {assignment.get('uid_serial')}) "
                f"不在来源快照 {snapshot_id} 中"
            )
            logger.error("已保存方案兼容性错误：{}", message)
            raise RuntimeError(message)
        unknown_properties = [
            str(stat.get("property_id") or "")
            for stat in (*item.get("main_stats", ()), *item.get("sub_stats", ()))
            if str(stat.get("property_id") or "") not in attribute_ids
        ]
        if unknown_properties:
            message=(
                f"方案 #{plan.get('plan_id')} 的来源快照包含当前静态数据未定义的属性 ID："
                f"{', '.join(sorted(set(unknown_properties)))}"
            )
            logger.error("已保存方案兼容性错误：{}", message)
            raise RuntimeError(message)
        uid_prefix="module" if item["kind"] == "module" else "core"
        uid=f"nte-{uid_prefix}-{item['uid_slot']}-{item['uid_serial']}"
        item_icon_path=warehouse_item_view(item).get("item_icon_path")
        if item["kind"] == "core":
            suit_id = str(item.get("suit_id") or "")
            if suit_id not in suit_names:
                message=(
                    f"方案 #{plan.get('plan_id')} 的核心套装 {suit_id or '<empty>'} "
                    "不在当前静态数据中"
                )
                logger.error("已保存方案兼容性错误：{}", message)
                raise RuntimeError(message)
            main_stats=_official_stat_values(item.get("main_stats"))
            tape={
                EQUIP_UID: uid, EQUIP_SET_NAME: suit_names.get(str(item.get("suit_id") or ""), str(item.get("suit_id") or "未知套装")),
                EQUIP_MAIN_STATS: next(iter(main_stats), "未知主词条"), EQUIP_SUB_STATS: _official_stat_values(item.get("sub_stats")),
                EQUIP_QUALITY: {"orange":"Gold","purple":"Purple","blue":"Blue"}.get(str(item.get("quality")).casefold(),"Gold"),
                "discarded": bool(item.get("discarded")),
                "item_icon_path": item_icon_path,
                "virtual": bool(item.get("virtual")),
                EQUIP_IS_CHANGED: uid in changed_uids,
                EQUIP_IS_NEW: uid in added_uids and uid not in changed_uids,
            }
            continue
        geometry=item.get("geometry")
        shape_id=_display_shape_id(geometry)
        official_shape="EquipmentGeometry_" + str(geometry or "").removeprefix("EquipmentGeometry_")
        if official_shape not in shape_cells:
            message=(
                f"方案 #{plan.get('plan_id')} 的驱动形状 {geometry or '<empty>'} "
                "不在当前静态数据中"
            )
            logger.error("已保存方案兼容性错误：{}", message)
            raise RuntimeError(message)
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
            "item_icon_path": item_icon_path,
            "virtual": bool(item.get("virtual")),
            EQUIP_IS_CHANGED: uid in changed_uids,
            EQUIP_IS_NEW: uid in added_uids and uid not in changed_uids,
        })
        row,column=assignment.get("target_row"),assignment.get("target_column")
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


def _saved_plan_graduation_info(role_name: str) -> dict | None:
    """Project the rebuilt role page's graduation result onto saved loadouts.

    The source of truth remains the role page's public SQLite detail model:
    saved plans supply only drive/core items, while profile settings retain the
    player's own account-side choices.
    """

    database_path = getattr(runtime, "USER_DATABASE_PATH", None)
    if database_path is None:
        return None
    try:
        with UserDataDao(database_path) as user_dao:
            plan = user_dao.get_active_loadout_plan_for_role(role_name)
        if not isinstance(plan, dict):
            return None
        character_id = int(plan.get("character_id"))
        from src.features.official_role.page import (
            _graduation_benchmark_damage,
            _graduation_tooltip,
        )
        from src.services.official_role_page_service import (
            calculate_official_role_margins,
            load_official_role_detail,
        )
        detail = load_official_role_detail(database_path, character_id)
        context_key = "saved" if (
            (detail.get("equipment_contexts") or {}).get("saved", {}).get("available")
        ) else "current"
        damage = float((calculate_official_role_margins(detail, context_key) or {}).get("damage") or 0.0)
        benchmark = _graduation_benchmark_damage(detail)
        if damage <= 0 or not benchmark:
            return None
        return {
            "text": f"毕业率 {damage / benchmark * 100:.1f}%",
            "tooltip": _graduation_tooltip(detail),
            "color": "#ffaa00",
        }
    except Exception as exc:
        logger.debug("配装毕业率加载失败 [{}]: {}", role_name, exc)
        return None


def _open_official_saved_plan_optimizer(
    window, role_name: str, item_kind: str, uid: str,
) -> bool:
    """Open the replacement flow backed by the new SQLite role panel.

    The old role editor was removed, but this inventory card action remained.
    Loading the official role detail here makes the panel data available before
    direct-damage evaluation and keeps the replacement calculation on the same
    SQLite path as the new role page.
    """
    with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
        plan = dao.get_active_loadout_plan_for_role(role_name)
    if not isinstance(plan, dict):
        return False
    character_id = plan.get("character_id")
    if character_id is None:
        return False
    from src.features.official_role.page import _show_replacement_optimizer
    from src.services.official_role_page_service import load_official_role_detail

    detail = load_official_role_detail(
        runtime.USER_DATABASE_PATH, int(character_id),
    )
    expected_kind = "module" if item_kind == "drive" else "core"
    target = next(
        (
            item for item in (detail.get("equipment_contexts", {}).get("saved", {}).get("items") or ())
            if str(item.get("kind") or "") == expected_kind
            and f"nte-{expected_kind}-{item.get('uid_slot')}-{item.get('uid_serial')}" == str(uid)
        ),
        None,
    )
    if not isinstance(target, dict):
        return False
    _show_replacement_optimizer(window, detail, target)
    return True


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
    if (
        rank_by_damage
        and weights_override is None
        and main_weights_override is None
    ):
        try:
            if _open_official_saved_plan_optimizer(self, role_name, item_kind, uid):
                return
        except Exception as exc:
            QMessageBox.warning(self, "优化替换", f"无法读取官方角色详情：{exc}")
            return
        QMessageBox.warning(self, "优化替换", "当前方案无法在官方角色详情中定位，请重新计算并保存后重试。")
        return
    try:
        plan, current, candidates, _plan_drives, _plan_tape = _sqlite_replacement_candidates(
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
    asset_catalog = GameUiAssetCatalog(runtime.ASSET_DIR / "game_ui")
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
        if rank_by_damage
        else "候选按当前词条配装权重评分排序"
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
                    DIFF_ADDED: [{
                        EQUIP_UID: str(selected.get(EQUIP_UID) or ""),
                        EQUIP_IS_CHANGED: True,
                    }],
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
    """Compatibility path for non-Qt callers; production uses viewport slots."""
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

def _render_equip_role(self, role_name, rd, *, target_layout=None):
    role_cfg=self.roles_db.get(role_name,{})
    wts=role_cfg.get("weights",{})
    graduation_info = rd.get("_graduation_info") if isinstance(rd, dict) else None
    if graduation_info is None and "_sqlite_plan_id" not in rd:
        # Retain the legacy/direct-call fallback; normal SQLite cards receive
        # this value from the loading worker.
        graduation_info = _saved_plan_graduation_info(role_name)
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
    # Graduation is a role-total metric, not an individual drive/core score.
    # Keep it immediately to the left of the role's overall score.
    if graduation_info:
        # It is a whole-role metric, so it deliberately shares the total
        # score/grade color and exact badge framing.
        graduation_color = gc
        graduation_bg = gbg
        graduation_frame = QFrame()
        graduation_frame.setStyleSheet(
            f"QFrame{{background:{graduation_bg};border:1px solid {graduation_color};"
            "border-radius:7px;padding:4px 12px}"
        )
        graduation_layout = QHBoxLayout(graduation_frame)
        graduation_layout.setSpacing(6)
        graduation_layout.setContentsMargins(4, 0, 4, 0)
        graduation_label = QLabel(str(graduation_info["text"]))
        graduation_label.setStyleSheet(
            f"font-size:14px;font-weight:800;color:{graduation_color};border:none"
        )
        graduation_label.setToolTip(str(graduation_info.get("tooltip") or ""))
        graduation_layout.addWidget(graduation_label)
        role_hdr.addWidget(graduation_frame)
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
        gl.addWidget(self._equip_card(tape_data.get(EQUIP_SET_NAME,""),tape_data.get(EQUIP_MAIN_STATS,""),tape_data.get(EQUIP_SUB_STATS,{}),None,tape_uid,wts,(t_s,t_g),t_q,is_new=bool(tape_data.get(EQUIP_IS_NEW)) and not tape_changed,is_changed=tape_changed,is_discarded=bool(tape_data.get("discarded")),main_weights=main_wts,replacement_callback=lambda rn=role_name, item_uid=tape_uid: self._optimize_saved_equipment(rn,"tape",item_uid),card_variant="inventory",item_icon_path=tape_data.get("item_icon_path")))
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
            gl.addWidget(self._equip_card(d.get(EQUIP_SHAPE_ID,""),"",d.get(EQUIP_SUB_STATS,{}),d.get(EQUIP_SHAPE_ID,""),drive_uid,wts,(d_s,d_g),d_q,is_new=bool(d.get(EQUIP_IS_NEW)) and not drive_changed,is_changed=drive_changed,is_discarded=bool(d.get("discarded")),is_duplicate_drive=bool(d.get("is_duplicate_drive")),replacement_callback=lambda rn=role_name, item_uid=drive_uid: self._optimize_saved_equipment(rn,"drive",item_uid),card_variant="inventory",item_icon_path=d.get("item_icon_path")))
    (target_layout or self.equip_content_layout).addWidget(grp)
    return grp

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
