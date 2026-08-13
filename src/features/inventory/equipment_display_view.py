# 加载、分页并惰性渲染当前账号已保存的配装方案。
"""MainWindow methods for inventory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
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
    normalized_equipment_assignment,
)
from src.ui.widgets import match_pinyin as _match_pinyin
from src.utils.logger import logger
from src.features.inventory.equipment_lazy_view import (
    capture_equipment_restore_anchor as _capture_equipment_restore_anchor,
    restore_equipment_anchor as _restore_equipment_anchor,
)
from src.features.inventory.equipment_master_detail_view import (
    build_equipment_master_detail,
    capture_equipment_navigation_state,
    clear_equipment_master_detail,
    filter_equipment_master_detail,
    show_equipment_master_detail,
    sorted_equipment_role_states,
)
from src.features.inventory.equipment_display_context import equipment_presentation
from src.features.inventory.equipment_loadout_scoring import (
    score_equipment_display_state,
)
from src.features.inventory.equipment_plan_optimizer import (
    _inventory_uid_key,
    _sqlite_inventory_item_display,
    _sqlite_plan_display_state,
)
from src.features.inventory.equipment_plan_renderer import (
    _render_equip_batch,
)


__all__ = [
    "_equipment_compare_signature",
    "_same_equipment_by_ocr",
    "_page_equipment",
    "_set_equipment_mode",
    "_refresh_equip",
    "_request_equipment_graduation_rate",
    "_capture_equipment_restore_anchor",
    "_restore_equipment_anchor",
    "_saved_plan_diff_text",
    "_show_saved_plan_diff_dialog",
    "_clear_all_equipment",
    "_delete_role_equipment",
    "_optimize_saved_equipment",
    "build_equipment_mode_switch",
]

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


def build_equipment_mode_switch(self: Any, parent: QWidget | None = None) -> QWidget:
    container = QWidget(parent)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    self.equip_saved_mode_btn = QPushButton("计算配装")
    self.equip_game_mode_btn = QPushButton("游戏配装")
    for button in (self.equip_saved_mode_btn, self.equip_game_mode_btn):
        button.setCheckable(True)
        button.setFixedWidth(104)
        button.setStyleSheet(
            themed_style(
                "QPushButton{padding:5px 14px;border:1px solid #30363d;"
                "border-radius:6px;background:#161b22;color:#8b949e}"
                "QPushButton:checked{background:#1f6feb;color:#ffffff;"
                "border-color:#58a6ff;font-weight:700}"
            )
        )
    self.equip_saved_mode_btn.setChecked(True)
    self.equip_saved_mode_btn.clicked.connect(
        lambda _checked=False: _set_equipment_mode(self, "saved")
    )
    self.equip_game_mode_btn.clicked.connect(
        lambda _checked=False: _set_equipment_mode(self, "game")
    )
    layout.addWidget(self.equip_saved_mode_btn)
    layout.addWidget(self.equip_game_mode_btn)
    return container


def _page_equipment(self):
    page = QWidget()
    l = QVBoxLayout(page)
    l.setContentsMargins(20, 16, 20, 16)
    l.setSpacing(8)
    if not hasattr(self, "equip_saved_mode_btn"):
        self._equipment_mode_switch_fallback = build_equipment_mode_switch(
            self,
            page,
        )
        self._equipment_mode_switch_fallback.hide()
    # Status remains available to callbacks and tests, but is intentionally not
    # inserted into the page: game mode uses the detail empty-state for errors.
    self.equip_mode_status = QLabel("", page)
    self.equip_mode_status.hide()
    sh = QHBoxLayout()
    sh.addWidget(QLabel("搜索"))
    self.equip_search = QLineEdit()
    self.equip_search.setPlaceholderText("搜索角色名称（支持拼音）...")
    self.equip_search.setClearButtonEnabled(True)
    self._equip_search_timer = QTimer(page)
    self._equip_search_timer.setSingleShot(True)
    self._equip_search_timer.setInterval(120)
    self._equip_search_timer.timeout.connect(
        lambda: filter_equipment_master_detail(self)
    )
    self.equip_search.textChanged.connect(lambda _text: self._equip_search_timer.start())
    sh.addWidget(self.equip_search, 1)
    self.equip_import_all_btn = QPushButton("一键导入")
    self.equip_import_all_btn.setObjectName("btnPrimary")
    self.equip_import_all_btn.setToolTip("导入全部完整且未锁定的游戏内方案")
    self.equip_import_all_btn.clicked.connect(
        lambda _checked=False: self._import_all_game_loadouts()
    )
    self.equip_import_all_btn.setVisible(False)
    sh.addWidget(self.equip_import_all_btn)
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
    self._equip_saved_action_buttons = (clear_btn, fast_btn, automatic_btn)
    self._equipment_mode = getattr(self, "_equipment_mode", "saved")
    l.addLayout(sh)
    build_equipment_master_detail(self, l)
    return page


def _set_equipment_mode(self: Any, mode: str) -> None:
    selected = "game" if mode == "game" else "saved"
    if getattr(self, "_equipment_mode", "saved") == selected:
        self.equip_saved_mode_btn.setChecked(selected == "saved")
        self.equip_game_mode_btn.setChecked(selected == "game")
        return
    self._equipment_mode = selected
    self.equip_saved_mode_btn.setChecked(selected == "saved")
    self.equip_game_mode_btn.setChecked(selected == "game")
    for button in getattr(self, "_equip_saved_action_buttons", ()):
        button.setVisible(selected == "saved")
    self.equip_import_all_btn.setVisible(selected == "game")
    self.equip_import_all_btn.setEnabled(False)
    self.equip_mode_status.setText(
        "正在读取最近一次 nte-core 游戏装备…"
        if selected == "game"
        else ""
    )
    self._refresh_equip()


def _clear_equip_content(self):
    clear_equipment_master_detail(self)


def _request_equipment_graduation_rate(
    self: Any,
    role_name: str,
    state: dict[str, Any],
    value_label: QLabel,
    tooltip_widget: QWidget | tuple[QWidget, ...] | None = None,
) -> None:
    from src.features.inventory.equipment_graduation_controller import (
        request_equipment_graduation_rate,
    )

    request_equipment_graduation_rate(
        self,
        role_name,
        state,
        value_label,
        tooltip_widget,
        database_path=_equipment_paths(self)[0],
    )


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
        historical_plans = user_dao.list_loadout_plans()
        snapshot_ids = {
            int(plan["source_snapshot_id"]) for plan in plans.values() if plan.get("source_snapshot_id") is not None
        }
        assignment_uids_by_snapshot: dict[int, set[tuple[int, int]]] = {
            snapshot_id: set() for snapshot_id in snapshot_ids
        }
        diff_uids_by_snapshot: dict[int, set[tuple[int, int]]] = {
            snapshot_id: set() for snapshot_id in snapshot_ids
        }
        historical_snapshot_by_uid: dict[tuple[int, int], int] = {}
        for historical_plan in historical_plans:
            historical_snapshot_id = historical_plan.get("source_snapshot_id")
            if historical_snapshot_id is None:
                continue
            for assignment in historical_plan.get("assignments") or ():
                resolved = normalized_equipment_assignment(assignment)
                if is_virtual_equipment_assignment(resolved):
                    continue
                try:
                    key = (
                        int(resolved["uid_serial"]),
                        int(resolved["uid_slot"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if min(key) < 1:
                    continue
                # list_loadout_plans is newest-first. Preserve the most recent
                # immutable plan snapshot that actually contained this UID.
                historical_snapshot_by_uid.setdefault(
                    key,
                    int(historical_snapshot_id),
                )
        for plan in plans.values():
            snapshot_id = int(plan["source_snapshot_id"])
            for assignment in plan.get("assignments") or ():
                resolved = normalized_equipment_assignment(assignment)
                if is_virtual_equipment_assignment(resolved):
                    continue
                try:
                    key = (
                        int(resolved["uid_serial"]),
                        int(resolved["uid_slot"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if min(key) >= 1:
                    assignment_uids_by_snapshot[snapshot_id].add(key)
            last_diff = (plan.get("payload") or {}).get("last_diff") or {}
            for diff_key in ("removed", "added"):
                for diff_item in last_diff.get(diff_key, ()) or ():
                    uid = diff_item.get("uid") if isinstance(diff_item, dict) else diff_item
                    key = _inventory_uid_key(uid)
                    if key is not None:
                        diff_uids_by_snapshot[snapshot_id].add(key)
        inventory_by_snapshot = {
            snapshot_id: {
                (row["uid_serial"], row["uid_slot"]): row
                for row in user_dao.list_inventory_items(
                    snapshot_id,
                    uids=(
                        assignment_uids_by_snapshot[snapshot_id]
                        | diff_uids_by_snapshot[snapshot_id]
                    ),
                )
            }
            for snapshot_id in snapshot_ids
        }
        historical_diff_requests: dict[int, set[tuple[int, int]]] = {}
        for snapshot_id, diff_uids in diff_uids_by_snapshot.items():
            missing_uids = diff_uids - set(inventory_by_snapshot[snapshot_id])
            for uid in missing_uids:
                historical_snapshot_id = historical_snapshot_by_uid.get(uid)
                if historical_snapshot_id is not None:
                    historical_diff_requests.setdefault(
                        historical_snapshot_id,
                        set(),
                    ).add(uid)
        historical_diff_items: dict[tuple[int, int], dict[str, Any]] = {}
        for snapshot_id, uids in historical_diff_requests.items():
            for row in user_dao.list_inventory_items(snapshot_id, uids=uids):
                historical_diff_items[(row["uid_serial"], row["uid_slot"])] = row
        for snapshot_id, diff_uids in diff_uids_by_snapshot.items():
            for uid in diff_uids:
                if uid not in inventory_by_snapshot[snapshot_id] and uid in historical_diff_items:
                    inventory_by_snapshot[snapshot_id][uid] = historical_diff_items[uid]
        shape_cells = {shape["shape_id"]: shape.get("cells") or [] for shape in static_dao.list_shapes()}
        suit_names = {
            str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"]) for suit in static_dao.list_suits()
        }
        attribute_ids = {str(attribute["attribute_id"]) for attribute in static_dao.list_equipment_attributes()}
        displays = {}
        snapshot_sources = {
            snapshot_id: str(
                (user_dao.inventory_snapshot_summary(snapshot_id) or {}).get(
                    "source"
                )
                or ""
            )
            for snapshot_id in snapshot_ids
        }
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
            display["_character_id"] = int(plan["character_id"])
            display["_sqlite_snapshot_source"] = snapshot_sources.get(
                int(plan["source_snapshot_id"]),
                "",
            )
            displays[role_name] = display
        return displays


def _load_game_equipment_display_states(
    database_path,
    *,
    static_database_path=None,
    saved_states=None,
):
    from src.optimizer.contracts import (
        ROLE_BLUEPRINT_LAYOUT,
        ROLE_EQUIPPED_DRIVES,
        ROLE_EQUIPPED_TAPE,
        ROLE_LAST_DIFF,
        ROLE_TOTAL_GRADE,
        ROLE_TOTAL_SCORE,
    )
    from src.services.game_loadout_projection_service import (
        GameLoadoutProjectionService,
    )

    if saved_states is None:
        try:
            saved_states = _load_sqlite_equipment_display_states(
                database_path,
                static_database_path=static_database_path,
            )
        except Exception:
            logger.warning("游戏配装加载计算配装对比数据失败，本次仅展示游戏配装")
            saved_states = {}
    else:
        saved_states = dict(saved_states)

    with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
        projection = GameLoadoutProjectionService(user_dao, static_dao).project_current()
        if not projection.supported:
            return {
                "projection": projection,
                "states": {},
                "saved_states": saved_states,
            }
        suit_names = {
            str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"])
            for suit in static_dao.list_suits()
        }
        shape_cells = {
            shape["shape_id"]: shape.get("cells") or []
            for shape in static_dao.list_shapes()
        }
        attribute_ids = {
            str(attribute["attribute_id"])
            for attribute in static_dao.list_equipment_attributes()
        }
        inventory = {
            (int(item["uid_serial"]), int(item["uid_slot"])): dict(item)
            for role in projection.roles
            for item in role.items
        }
        inventory_by_snapshot = {
            int(projection.snapshot_id): inventory
        } if projection.snapshot_id is not None else {}
        states = {}
        for role in projection.roles:
            if role.importable:
                plan = {
                    "plan_id": 0,
                    "source_snapshot_id": role.snapshot_id,
                    "score": None,
                    "payload": {"strategy": "game_inventory"},
                    "assignments": [dict(item) for item in role.assignments],
                    "allocation_locked": False,
                }
                state = _sqlite_plan_display_state(
                    plan,
                    user_dao,
                    static_dao,
                    inventory_by_snapshot=inventory_by_snapshot,
                    shape_cells=shape_cells,
                    suit_names=suit_names,
                    attribute_ids=attribute_ids,
                )
                state.pop("_sqlite_plan_id", None)
            else:
                display_items = [
                    _sqlite_inventory_item_display(item, suit_names)
                    for item in role.items
                ]
                state = {
                    ROLE_BLUEPRINT_LAYOUT: [],
                    ROLE_EQUIPPED_TAPE: next(
                        (
                            item
                            for raw, item in zip(role.items, display_items)
                            if raw.get("kind") == "core"
                        ),
                        None,
                    ),
                    ROLE_EQUIPPED_DRIVES: [
                        item
                        for raw, item in zip(role.items, display_items)
                        if raw.get("kind") == "module"
                    ],
                    ROLE_TOTAL_SCORE: 0.0,
                    ROLE_TOTAL_GRADE: "",
                    ROLE_LAST_DIFF: {},
                    "strategy_mode": "game_inventory",
                }
            state.update({
                "_character_id": role.character_id,
                "_game_mode": True,
                "_game_projection": role,
                "_game_importable": role.importable,
                "_game_status": role.status,
                "_game_reason": role.reason,
                "_game_imported": role.imported,
                "_game_existing_plan_id": role.existing_plan_id,
                "_game_existing_plan_name": role.existing_plan_name,
                "_game_existing_plan_locked": role.existing_plan_locked,
            })
            saved_state = saved_states.get(role.role_name)
            if isinstance(saved_state, dict):
                state["_game_saved_state"] = saved_state
            states[role.role_name] = state
        return {
            "projection": projection,
            "states": states,
            "saved_states": saved_states,
        }


def _queue_equipment_render(self, eq):
    filt = self.equip_search.text().strip() if hasattr(self, "equip_search") else ""
    roles = []
    for role_name, rd in sorted_equipment_role_states(eq):
        if filt and not _match_pinyin(role_name, filt):
            continue
        roles.append((role_name, rd))

    self._equip_render_token = object()
    token = self._equip_render_token
    self._equip_render_queue = roles
    self._equip_lazy_entries = []

    empty_text = (
        getattr(self, "_game_loadout_message", "")
        if getattr(self, "_equipment_mode", "saved") == "game"
        else "暂无已保存的配装。请先执行分配并保存。"
    )
    if isinstance(self, QWidget):
        show_equipment_master_detail(
            self,
            roles,
            empty_message=(
                empty_text or "当前游戏快照中没有可展示的角色装备。"
            ),
        )
        return

    if not roles:
        ph = QLabel(empty_text or "当前游戏快照中没有可展示的角色装备。")
        ph.setStyleSheet(themed_style("color:#6e7681;padding:24px"))
        ph.setAlignment(Qt.AlignCenter)
        self.equip_content_layout.addWidget(ph)
        self.equip_content_layout.addStretch()
        return

    self._equip_render_queue = roles
    self._equip_render_index = 0
    self._equip_render_stretch_added = False
    _render_equip_batch(self, token, EQUIPMENT_INITIAL_RENDER_COUNT)


def _on_sqlite_equipment_display_loaded(self, token, eq):
    if (
        token is not getattr(self, "_equip_load_token", None)
        or getattr(self, "_equipment_mode", "saved") != "saved"
    ):
        return
    states = eq if isinstance(eq, dict) else {}
    self.equip_mode_status.setText("")
    self._saved_equipment_states = dict(states)
    self._saved_equipment_cache_valid = True
    _clear_equip_content(self)
    _queue_equipment_render(self, states)


def _on_sqlite_equipment_display_error(self, token, error):
    if token is not getattr(self, "_equip_load_token", None):
        return
    logger.error(f"刷新 SQLite 配装展示失败: {error}")
    self.equip_mode_status.setText("读取已保存配装失败")
    QMessageBox.warning(
        self,
        "已保存方案兼容性错误",
        "无法按当前官方静态数据解释部分已保存方案。"
        "方案未被修改；请查看日志中的形状、套装或属性 ID 后再决定是否重新计算。\n\n"
        f"详细原因：{error}",
    )
    _clear_equip_content(self)
    self._saved_equipment_states = {}
    self._saved_equipment_cache_valid = False
    _queue_equipment_render(self, {})


def _on_game_equipment_display_loaded(self, token, result):
    if (
        token is not getattr(self, "_equip_load_token", None)
        or getattr(self, "_equipment_mode", "saved") != "game"
    ):
        return
    projection = result.get("projection") if isinstance(result, dict) else None
    states = result.get("states", {}) if isinstance(result, dict) else {}
    saved_states = (
        result.get("saved_states", {}) if isinstance(result, dict) else {}
    )
    if projection is None:
        _on_sqlite_equipment_display_error(self, token, "游戏内方案投影结果无效")
        return
    if projection.supported:
        ready_count = sum(
            isinstance(state, dict)
            and bool(state.get("_game_importable"))
            and not bool(state.get("_game_imported"))
            and not bool(state.get("_game_existing_plan_locked"))
            for state in states.values()
        )
        self.equip_mode_status.setText("")
        self.equip_import_all_btn.setEnabled(ready_count > 0)
        self.equip_import_all_btn.setToolTip(
            f"导入全部 {ready_count} 套完整且未锁定的游戏内方案"
        )
        self._game_loadout_message = (
            "当前游戏快照中没有已装备驱动或卡带。"
            if not projection.roles
            else ""
        )
    else:
        self.equip_mode_status.setText(projection.message)
        self._game_loadout_message = projection.message
        self.equip_import_all_btn.setEnabled(False)
    scored_states = dict(states)
    try:
        presentation = equipment_presentation(self)
        for role_name, state in scored_states.items():
            if isinstance(state, dict):
                score_equipment_display_state(
                    presentation,
                    role_name,
                    state,
                    getattr(self, "roles_db", {}) or {},
                )
    except Exception as exc:
        logger.warning(f"游戏配装评分投影失败: {exc}")
    self._game_loadout_states = scored_states
    self._saved_equipment_states = dict(saved_states)
    self._saved_equipment_cache_valid = True
    _clear_equip_content(self)
    _queue_equipment_render(self, scored_states)


def _on_game_equipment_display_error(self, token, error):
    if token is not getattr(self, "_equip_load_token", None):
        return
    logger.error(f"刷新游戏内配装展示失败: {error}")
    self.equip_mode_status.setText("读取游戏内装备失败")
    self.equip_import_all_btn.setEnabled(False)
    self._game_loadout_message = f"读取游戏内装备失败：{error}"
    _clear_equip_content(self)
    _queue_equipment_render(self, {})


def _refresh_equip(self, *, restore_role_name=None):
    database_path, static_database_path, _ = _equipment_paths(self)
    capture_equipment_navigation_state(self)
    if restore_role_name is None:
        mode = getattr(self, "_equipment_mode", "saved")
        selected_by_mode = getattr(self, "_equip_selected_role_by_mode", {})
        if isinstance(selected_by_mode, dict):
            selected = selected_by_mode.get(mode)
            restore_role_name = str(selected) if selected else None
    if restore_role_name:
        self._equip_pending_role_name = restore_role_name
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
        game_mode = getattr(self, "_equipment_mode", "saved") == "game"
        loading = QLabel(
            "正在读取游戏内装备…" if game_mode else "正在读取已保存的配装…"
        )
        loading.setStyleSheet(themed_style("color:#8b949e;padding:24px"))
        loading.setAlignment(Qt.AlignCenter)
        self.equip_content_layout.addWidget(loading)
        if game_mode:
            cached_saved_states = (
                dict(getattr(self, "_saved_equipment_states", {}) or {})
                if getattr(self, "_saved_equipment_cache_valid", False)
                else None
            )
            target = lambda cached=cached_saved_states: _load_game_equipment_display_states(
                database_path,
                static_database_path=static_database_path,
                saved_states=cached,
            )
        else:
            target = lambda: _load_sqlite_equipment_display_states(
                database_path,
                static_database_path=static_database_path,
            )
        worker = WorkerThread(target=target, parent=self)
        self._equip_load_worker = worker
        if game_mode:
            worker.result_ready.connect(
                lambda result, current=token: _on_game_equipment_display_loaded(
                    self, current, result,
                )
            )
            worker.error.connect(
                lambda error, current=token: _on_game_equipment_display_error(
                    self, current, error,
                )
            )
        else:
            worker.result_ready.connect(lambda eq, current=token: _on_sqlite_equipment_display_loaded(self, current, eq))
            worker.error.connect(lambda error, current=token: _on_sqlite_equipment_display_error(self, current, error))
        worker.start()
        return
    if getattr(self, "_equipment_mode", "saved") == "game":
        try:
            result = _load_game_equipment_display_states(
                database_path,
                static_database_path=static_database_path,
                saved_states=(
                    dict(getattr(self, "_saved_equipment_states", {}) or {})
                    if getattr(self, "_saved_equipment_cache_valid", False)
                    else None
                ),
            )
            projection = result["projection"]
            self._game_loadout_message = projection.message
            states = dict(result["states"])
            presentation = equipment_presentation(self)
            for role_name, state in states.items():
                if isinstance(state, dict):
                    score_equipment_display_state(
                        presentation,
                        role_name,
                        state,
                        getattr(self, "roles_db", {}) or {},
                    )
            self._game_loadout_states = states
            self._saved_equipment_states = dict(result.get("saved_states", {}))
            self._saved_equipment_cache_valid = True
            _queue_equipment_render(self, states)
        except Exception as exc:
            logger.error(f"刷新游戏内配装展示失败: {exc}")
            self._game_loadout_message = str(exc)
            _queue_equipment_render(self, {})
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
    self._saved_equipment_states = dict(eq)
    self._saved_equipment_cache_valid = True
    _queue_equipment_render(self, eq)
