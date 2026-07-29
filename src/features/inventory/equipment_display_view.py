# 加载、分页并惰性渲染当前账号已保存的配装方案。
"""MainWindow methods for inventory."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.workers import WorkerThread
from src.features.scanning.file_lifecycle import equipment_compare_signature
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
)
from src.ui.widgets import match_pinyin as _match_pinyin
from src.utils.logger import logger
from src.features.inventory.equipment_plan_optimizer import (
    _sqlite_plan_display_state,
)
from src.features.inventory.equipment_plan_renderer import (
    _render_equip_batch,
    _render_equip_role,
)


__all__ = [
    "_equipment_compare_signature",
    "_same_equipment_by_ocr",
    "_page_equipment",
    "_refresh_equip",
    "_saved_plan_diff_text",
    "_show_saved_plan_diff_dialog",
    "_clear_all_equipment",
    "_delete_role_equipment",
    "_optimize_saved_equipment",
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



def _equipment_paths(window) -> tuple[Path, Path, Path]:
    context = getattr(window, "app_context", None)
    if context is None:
        database_path = getattr(window, "user_database_path", None)
        static_database_path = getattr(
            window,
            "static_database_path",
            None,
        )
        asset_dir = getattr(window, "asset_dir", None)
        if database_path is None or static_database_path is None or asset_dir is None:
            raise RuntimeError("配装展示缺少 AppContext 或显式路径依赖")
        return (
            Path(database_path),
            Path(static_database_path),
            Path(asset_dir),
        )
    return (
        Path(context.account.user_database_path),
        Path(context.paths.static_database_path),
        Path(context.paths.asset_dir),
    )


def _equipment_compare_signature(self, item):
    return equipment_compare_signature(item)


def _same_equipment_by_ocr(self, left: Path, right: Path):
    return self._scan_lifecycle().same_equipment_by_ocr(left, right)


def _page_equipment(self):
    page = QWidget()
    l = QVBoxLayout(page)
    l.setContentsMargins(20, 16, 20, 16)
    l.setSpacing(8)
    sh = QHBoxLayout()
    sh.addWidget(QLabel("搜索"))
    self.equip_search = QLineEdit()
    self.equip_search.setPlaceholderText("搜索角色名称（支持拼音）...")
    self.equip_search.setClearButtonEnabled(True)
    self._equip_search_timer = QTimer(page)
    self._equip_search_timer.setSingleShot(True)
    self._equip_search_timer.setInterval(120)
    self._equip_search_timer.timeout.connect(self._refresh_equip)
    self.equip_search.textChanged.connect(lambda _text: self._equip_search_timer.start())
    sh.addWidget(self.equip_search, 1)
    clear_btn = QPushButton("清空配装")
    clear_btn.setObjectName("btnDanger")
    clear_btn.clicked.connect(self._clear_all_equipment)
    sh.addWidget(clear_btn)
    fast_btn = QPushButton("极速装配")
    fast_btn.setObjectName("btnPrimary")
    fast_btn.clicked.connect(self._preview_fast_assemble_all_roles)
    fast_btn.setToolTip("通过游戏内装备插件直接写入已保存方案")
    sh.addWidget(fast_btn)
    automatic_btn = QPushButton("自动装配")
    automatic_btn.setObjectName("btnPrimary")
    automatic_btn.clicked.connect(self._preview_automatic_assemble_all_roles)
    automatic_btn.setToolTip("模拟游戏内操作，逐步完成已保存方案")
    sh.addWidget(automatic_btn)
    l.addLayout(sh)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    self.equip_scroll = scroll
    self.equip_content = QWidget()
    self.equip_content_layout = QVBoxLayout(self.equip_content)
    scroll.setWidget(self.equip_content)
    scroll.verticalScrollBar().valueChanged.connect(lambda _value: _schedule_visible_equipment_render(self))
    scroll.verticalScrollBar().rangeChanged.connect(lambda _minimum, _maximum: _schedule_equipment_restore_anchor(self))
    l.addWidget(scroll, 1)
    return page


def _clear_equip_content(self):
    while self.equip_content_layout.count():
        it = self.equip_content_layout.takeAt(0)
        if it.widget():
            it.widget().deleteLater()


def _capture_equipment_restore_anchor(self, preferred_role_name=None):
    """Record a role-card anchor before asynchronously rebuilding the page.

    A scrollbar value is not stable here: cards begin as fixed-height
    placeholders and grow after their lazy content is rendered. Keeping the
    role and its viewport offset lets us restore the user's actual context
    after that geometry settles.
    """
    scroll = getattr(self, "equip_scroll", None)
    if scroll is None:
        return None
    bar = scroll.verticalScrollBar()
    entries = list(getattr(self, "_equip_lazy_entries", []) or [])
    target = next(
        (entry for entry in entries if entry.get("role_name") == preferred_role_name),
        None,
    )
    if target is None:
        viewport = scroll.viewport()
        viewport_top = bar.value()
        viewport_bottom = viewport_top + max(1, viewport.height())
        target = next(
            (
                entry
                for entry in entries
                if (slot := entry.get("slot")) is not None
                and slot.y() + slot.height() > viewport_top
                and slot.y() < viewport_bottom
            ),
            None,
        )
    anchor = {
        "role_name": target.get("role_name") if target else preferred_role_name,
        "viewport_offset": None,
        "scroll_value": bar.value(),
        "load_token": None,
        "render_token": None,
        "attempts": 0,
        "scheduled": False,
    }
    if target is not None and target.get("slot") is not None:
        slot_top = scroll.viewport().mapFromGlobal(target["slot"].mapToGlobal(QPoint(0, 0))).y()
        anchor["viewport_offset"] = slot_top
    return anchor


def _schedule_equipment_restore_anchor(self, token=None):
    anchor = getattr(self, "_equip_restore_anchor", None)
    if not isinstance(anchor, dict) or anchor.get("scheduled"):
        return
    render_token = anchor.get("render_token")
    if render_token is None or (token is not None and token is not render_token):
        return
    if render_token is not getattr(self, "_equip_render_token", None):
        return
    anchor["scheduled"] = True
    QTimer.singleShot(50, lambda current=render_token: _restore_equipment_anchor(self, current))


def _restore_equipment_anchor(self, token):
    anchor = getattr(self, "_equip_restore_anchor", None)
    if not isinstance(anchor, dict) or anchor.get("render_token") is not token:
        return
    anchor["scheduled"] = False
    if token is not getattr(self, "_equip_render_token", None):
        return
    scroll = getattr(self, "equip_scroll", None)
    if scroll is None:
        self._equip_restore_anchor = None
        return
    entry = next(
        (item for item in getattr(self, "_equip_lazy_entries", []) if item.get("role_name") == anchor.get("role_name")),
        None,
    )
    if entry is not None and not entry.get("loaded"):
        _render_lazy_equipment_entry(self, entry)
    if entry is not None and entry.get("slot") is not None:
        slot_top = entry["slot"].mapTo(self.equip_content, QPoint(0, 0)).y()
        offset = anchor.get("viewport_offset")
        desired = slot_top - int(offset) if offset is not None else slot_top
        scroll.verticalScrollBar().setValue(max(0, desired))
    else:
        scroll.verticalScrollBar().setValue(int(anchor.get("scroll_value") or 0))
    anchor["attempts"] = int(anchor.get("attempts") or 0) + 1
    # Card height can still change once queued rendering completes. A few
    # event-loop turns are enough to settle it while
    # avoiding a persistent fight against a user's later manual scrolling.
    if anchor["attempts"] < 8:
        _schedule_equipment_restore_anchor(self, token)
    else:
        self._equip_restore_anchor = None


def _load_sqlite_equipment_display_states(
    database_path,
    *,
    static_database_path=None,
):
    """Read display-only saved plans off the UI thread with shared snapshots.

    A multi-role allocation commonly binds every plan to one immutable
    snapshot.  Re-reading that snapshot and static catalogs per card was the
    principal loading bottleneck.
    """
    with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
        plans = user_dao.list_active_loadout_plans_by_role()
        snapshot_ids = {
            int(plan["source_snapshot_id"]) for plan in plans.values() if plan.get("source_snapshot_id") is not None
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
                    referenced_uids_by_snapshot[snapshot_id].add(
                        (
                            int(assignment["uid_serial"]),
                            int(assignment["uid_slot"]),
                        )
                    )
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
        shape_cells = {shape["shape_id"]: shape.get("cells") or [] for shape in static_dao.list_shapes()}
        suit_names = {
            str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"]) for suit in static_dao.list_suits()
        }
        attribute_ids = {str(attribute["attribute_id"]) for attribute in static_dao.list_equipment_attributes()}
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
    all_roles = sorted(eq.keys())
    filt = self.equip_search.text().strip() if hasattr(self, "equip_search") else ""
    roles = []
    for role_name in all_roles:
        if filt and not _match_pinyin(role_name, filt):
            continue
        rd = eq.get(role_name, {})
        if not isinstance(rd, dict):
            continue
        roles.append((role_name, rd))

    self._equip_render_token = object()
    token = self._equip_render_token
    self._equip_render_queue = roles
    self._equip_lazy_entries = []

    if not roles:
        ph = QLabel("暂无已保存的配装。请先执行分配并保存。")
        ph.setStyleSheet(themed_style("color:#6e7681;padding:24px"))
        ph.setAlignment(Qt.AlignCenter)
        self.equip_content_layout.addWidget(ph)
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
        self._equip_lazy_entries.append(
            {
                "role_name": role_name,
                "state": rd,
                "slot": slot,
                "layout": slot_layout,
                "loaded": False,
            }
        )
    self.equip_content_layout.addStretch()
    _schedule_visible_equipment_render(self, token)


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
        self,
        entry["role_name"],
        entry["state"],
        target_layout=layout,
    )
    entry["loaded"] = True
    entry["slot"].setFixedHeight(
        max(
            EQUIPMENT_ROLE_PLACEHOLDER_HEIGHT,
            group.sizeHint().height() + 8,
        )
    )
    _schedule_equipment_restore_anchor(self)


def _render_visible_equipment_roles(self, token):
    if token is not getattr(self, "_equip_render_token", None):
        return
    scroll = getattr(self, "equip_scroll", None)
    if scroll is None:
        return
    viewport_top = scroll.verticalScrollBar().value()
    viewport_height = max(1, scroll.viewport().height())
    viewport_bottom = viewport_top + viewport_height * (1 + EQUIPMENT_VIEWPORT_PREFETCH_COUNT)
    anchor = getattr(self, "_equip_restore_anchor", None)
    if isinstance(anchor, dict) and anchor.get("render_token") is token:
        target = next(
            (
                entry
                for entry in getattr(self, "_equip_lazy_entries", [])
                if entry.get("role_name") == anchor.get("role_name")
            ),
            None,
        )
        if target is not None and not target.get("loaded"):
            _render_lazy_equipment_entry(self, target)
    for entry in getattr(self, "_equip_lazy_entries", []):
        if entry["loaded"]:
            continue
        slot = entry["slot"]
        if slot.y() > viewport_bottom or slot.y() + slot.height() < viewport_top - viewport_height:
            continue
        _render_lazy_equipment_entry(self, entry)
    _schedule_equipment_restore_anchor(self, token)


def _on_sqlite_equipment_display_loaded(self, token, eq):
    if token is not getattr(self, "_equip_load_token", None):
        return
    _clear_equip_content(self)
    _queue_equipment_render(self, eq if isinstance(eq, dict) else {})
    anchor = getattr(self, "_equip_restore_anchor", None)
    if isinstance(anchor, dict) and anchor.get("load_token") is token:
        anchor["render_token"] = getattr(self, "_equip_render_token", None)
        _schedule_equipment_restore_anchor(self, anchor["render_token"])


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
    self._equip_restore_anchor = None
    _queue_equipment_render(self, {})


def _refresh_equip(self, *, restore_role_name=None):
    database_path, static_database_path, _ = _equipment_paths(self)
    self._equip_restore_anchor = _capture_equipment_restore_anchor(
        self,
        preferred_role_name=restore_role_name,
    )
    # Invalidate old lazy-render callbacks while the SQLite read runs.
    # Previously they could redraw deleted slots between the two clears below.
    self._equip_render_token = object()
    self._equip_lazy_entries = []
    self._equip_render_queue = []
    _clear_equip_content(self)
    # The production page may contain many plans and each requires snapshot
    # projection.  Keep database work off the Qt event loop; plain test hosts
    # retain the direct path below.
    if isinstance(self, QWidget):
        token = object()
        self._equip_load_token = token
        anchor = getattr(self, "_equip_restore_anchor", None)
        if isinstance(anchor, dict):
            anchor["load_token"] = token
        loading = QLabel("正在读取已保存的配装…")
        loading.setStyleSheet(themed_style("color:#8b949e;padding:24px"))
        loading.setAlignment(Qt.AlignCenter)
        self.equip_content_layout.addWidget(loading)
        worker = WorkerThread(
            target=lambda: _load_sqlite_equipment_display_states(
                database_path,
                static_database_path=static_database_path,
            ),
            parent=self,
        )
        self._equip_load_worker = worker
        worker.result_ready.connect(lambda eq, current=token: _on_sqlite_equipment_display_loaded(self, current, eq))
        worker.error.connect(lambda error, current=token: _on_sqlite_equipment_display_error(self, current, error))
        worker.start()
        return
    try:
        with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
            plans = user_dao.list_active_loadout_plans_by_role()
            eq = {
                role_name: _sqlite_plan_display_state(plan, user_dao, static_dao) for role_name, plan in plans.items()
            }
    except Exception as exc:
        logger.error(f"刷新 SQLite 配装展示失败: {exc}")
        eq = {}
    _queue_equipment_render(self, eq)
