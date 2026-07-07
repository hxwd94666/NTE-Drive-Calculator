# 构建角色功能主页面和保存同步逻辑。
"""角色详情编辑页面 (my_roles.json)."""

from __future__ import annotations

import json
import re
from copy import deepcopy

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QGroupBox,
    QDialog,
    QTableWidget,
    QTableWidgetItem,
    QInputDialog,
    QComboBox,
    QFrame
)

from PySide6.QtWidgets import QHeaderView
from PySide6.QtCore import Qt
from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.ui.puzzle_board import PuzzleBoardWidget
from src.ui.widgets import NoWheelDoubleSpinBox, SearchableComboBox, match_pinyin

from .paths import get_my_roles_model_path, get_roles_img_path
from .dao import (
    load_my_roles,
    save_my_roles,
    load_role_order,
    save_role_order,
    load_stats,
    load_weapons,
    load_tapes,
    load_real_inventory,
    merge_new_roles_from_model,
)
from .core import apply_margins_to_weights
from .marginal_widget import MarginalBenefitPanel
from .base_widget import BaseStatsWidget
from .drive_widget import build_drive_group, show_drive_details
from .weapon_widget import build_weapon_group, refresh_weapon_group
from .tape_widget import build_tape_group, refresh_tape_group
from .weight_widget import build_weight_group, refresh_weight_group

__all__ = ["_page_my_role", "_refresh_my_role", "confirm_pending_my_role_changes", "install_methods"]


def install_methods(app_module, window_cls):
    """Install feature methods onto the main window class."""
    window_cls._page_my_role = _page_my_role
    window_cls._refresh_my_role = _refresh_my_role


def _page_my_role(window) -> QWidget:
    """构建“角色”页面 (my_roles.json 编辑器)."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    page.setStyleSheet(
        """
        QLabel{font-size:14px}
        QLineEdit,QComboBox,QDoubleSpinBox{font-size:14px;padding:8px 11px;border-radius:7px}
        QPushButton{font-size:13px;padding:8px 15px;border-radius:7px}
        QTabBar::tab{font-size:13px;padding:10px 20px}
        QGroupBox{font-size:15px;border:1px solid #30363d;border-radius:10px;padding:24px;padding-top:36px}
        """
    )

    top_row = QHBoxLayout()
    top_row.addWidget(QLabel("编辑角色详情 (my_roles.json):"))
    top_row.addStretch()
    reset_btn = QPushButton("重置")
    reset_btn.setObjectName("btnDanger")
    save_btn = QPushButton("保存")
    save_btn.setObjectName("btnPrimary")

    def _on_reset():
        _reset_my_roles_from_model(window)

    def _on_save():
        _flush_role_widgets(window)
        _save_my_roles(window)

    reset_btn.clicked.connect(_on_reset)
    save_btn.clicked.connect(_on_save)
    top_row.addWidget(reset_btn)
    top_row.addWidget(save_btn)
    layout.addLayout(top_row)

    window.my_role_form_area = QScrollArea()
    window.my_role_form_area.setWidgetResizable(True)
    window.my_role_form_widget = QWidget()
    window.my_role_form_layout = QVBoxLayout(window.my_role_form_widget)
    window.my_role_form_area.setWidget(window.my_role_form_widget)
    layout.addWidget(window.my_role_form_area, 1)
    return page


def _refresh_my_role(window):
    """刷新角色编辑页面内容."""
    if not hasattr(window, "my_role_form_layout"):
        return
    _render_my_roles(window)


def _save_my_roles(window):
    """保存当前编辑的数据到 my_roles.json，并刷新界面。"""
    _flush_role_widgets(window)
    data = getattr(window, "_my_role_form_data", None)
    if data is None:
        QMessageBox.information(window, "提示", "没有需要保存的数据。")
        return False
    if save_my_roles(data):
        try:
            _save_pending_role_equipment_state(window, data)
        except Exception as exc:
            QMessageBox.warning(window, "保存失败", f"角色配置已保存，但同步配装锁定失败：{exc}")
            return False
        window._my_role_dirty = False
        QMessageBox.information(window, "保存", "my_roles.json 已保存")
        # 刷新界面
        _refresh_my_role(window)
        return True
    else:
        QMessageBox.warning(window, "保存失败", "保存 my_roles.json 失败")
        return False


def _reset_my_roles_from_model(window):
    """使用模板整体重置角色功能配置。"""
    ret = QMessageBox.question(
        window,
        "重置角色配置",
        "确定要用模板重置全部角色配置吗？\n"
        "这会覆盖角色基础配置、权重等模板字段，但会保留同名角色已导入的驱动和空幕。\n"
        "模板中不存在的自定义角色会被移除。",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret != QMessageBox.Yes:
        return False
    model_path = get_my_roles_model_path()
    if not model_path.exists():
        QMessageBox.warning(window, "重置失败", "未找到 my_roles_model.json。")
        return False
    try:
        with open(model_path, "r", encoding="utf-8") as f:
            model_data = json.load(f)
        if not isinstance(model_data, dict):
            raise ValueError("my_roles_model.json 格式不是对象")

        current_data = getattr(window, "_my_role_form_data", None)
        if not isinstance(current_data, dict):
            current_data = load_my_roles()

        reset_data = {}
        for role_name, model_role in model_data.items():
            if not isinstance(model_role, dict):
                reset_data[role_name] = deepcopy(model_role)
                continue
            role_entry = deepcopy(model_role)
            current_role = current_data.get(role_name, {})
            if isinstance(current_role, dict):
                if isinstance(current_role.get("drive"), dict):
                    role_entry["drive"] = deepcopy(current_role["drive"])
                if isinstance(current_role.get("tape"), dict):
                    role_entry["tape"] = deepcopy(current_role["tape"])
                if isinstance(current_role.get("set_bonus"), dict):
                    role_entry["set_bonus"] = deepcopy(current_role["set_bonus"])
            reset_data[role_name] = role_entry

        if not save_my_roles(reset_data):
            raise IOError("写入 my_roles.json 失败")
        window._my_role_form_data = reset_data
        window._my_role_dirty = False
        window._my_role_equipment_dirty_roles = set()
        _refresh_my_role(window)
        QMessageBox.information(window, "重置完成", "角色功能配置已按模板重置，并保留已导入的驱动和空幕。")
        return True
    except Exception as exc:
        QMessageBox.warning(window, "重置失败", str(exc))
        return False


def _mark_my_role_dirty(window):
    """标记角色页面有未保存修改."""
    window._my_role_dirty = True


def _role_drive_state(window, role_name, role_data, old_role_state):
    drives = []
    weights = getattr(window, "roles_db", {}).get(role_name, {}).get("weights", {})
    for drive in role_data.get("drive", {}).get("drives", []) or []:
        uid = str(drive.get("uid", "") or "")
        if not uid or uid.startswith("empty_"):
            continue
        shape_id = drive.get("shape_id", "")
        quality = drive.get("quality", "Gold")
        sub_stats = drive.get("sub_stats", {}) or {}
        area = int(getattr(window, "_shape_areas", {}).get(shape_id, 3) or 3)
        score = 0.0
        if hasattr(window, "_score_drive_dict"):
            score = window._score_drive_dict(sub_stats, shape_id, weights, quality)
        grade = window._calc_grade(score, area) if hasattr(window, "_calc_grade") else "D"
        drive_entry = {
            "uid": uid,
            "display_name": drive.get("display_name") or f"{shape_id}-" + "|".join(f"{k}_{v}" for k, v in sub_stats.items()),
            "shape_id": shape_id,
            "sub_stats": sub_stats,
            "quality": quality,
            "score": round(float(score or 0.0), 2),
            "grade": grade,
            "score_area": area,
        }
        if drive.get("is_changed"):
            drive_entry["is_changed"] = True
        drives.append(drive_entry)

    board = role_data.get("drive", {}).get("blueprint_layout", []) or []
    role_state = dict(old_role_state or {})
    role_state["blueprint_layout"] = board
    role_state["equipped_drives"] = drives

    tape = role_data.get("tape", {})
    equipped_tape = None
    if isinstance(tape, dict):
        uid = str(tape.get("uid", "") or "")
        if uid and not uid.startswith("empty_"):
            main_stats = tape.get("main_stats", {}) or {}
            main_stat_name = next(iter(main_stats.keys()), "") if isinstance(main_stats, dict) else str(main_stats or "")
            sub_stats = tape.get("sub_stats", {}) or {}
            quality = tape.get("quality", "Gold")
            score = 0.0
            if hasattr(window, "_score_tape_dict"):
                role_cfg = getattr(window, "roles_db", {}).get(role_name, {})
                try:
                    score = window._score_tape_dict(main_stat_name, sub_stats, weights, quality, role_cfg.get("main_weights"))
                except TypeError:
                    score = window._score_tape_dict(main_stat_name, sub_stats, weights, quality)
            grade = window._calc_grade(score, 15) if hasattr(window, "_calc_grade") else "D"
            equipped_tape = {
                "uid": uid,
                "display_name": tape.get("display_name") or tape.get("set_name") or "卡带",
                "set_name": tape.get("set_name", ""),
                "main_stats": main_stat_name,
                "sub_stats": sub_stats,
                "quality": quality,
                "score": round(float(score or 0.0), 2),
                "grade": grade,
                "score_area": 15,
            }
            if tape.get("is_changed"):
                equipped_tape["is_changed"] = True
    role_state["equipped_tape"] = equipped_tape

    total = float((role_state.get("equipped_tape") or {}).get("score", 0.0) or 0.0)
    total += sum(float(drive.get("score", 0.0) or 0.0) for drive in drives)
    role_state["total_score"] = round(total, 2)
    role_state["total_grade"] = window._calc_grade(total, ALLOCATION_TOTAL_SCORE_AREA) if hasattr(window, "_calc_grade") else "D"
    role_state["score_area"] = ALLOCATION_TOTAL_SCORE_AREA
    return role_state


def _save_pending_role_equipment_state(window, data):
    """将角色页驱动替换同步到 equipped_state.json。"""
    dirty_roles = set(getattr(window, "_my_role_equipment_dirty_roles", set()) or set())
    if not dirty_roles:
        return
    state_mgr = getattr(window, "state_mgr", None)
    if state_mgr is None:
        return
    old_state = state_mgr.load_state()
    new_state = dict(old_state or {})
    last_diffs = {}
    for role_name in sorted(dirty_roles):
        role_data = data.get(role_name)
        if not isinstance(role_data, dict):
            continue
        role_state = _role_drive_state(window, role_name, role_data, old_state.get(role_name, {}))
        role_diff = state_mgr._build_role_diff(old_state.get(role_name), role_state)
        last_diffs[role_name] = role_diff
        role_state.pop("last_diff", None)
        for item in role_state.get("equipped_drives", []) or []:
            item.pop("is_new", None)
        if role_diff.get("changed"):
            added_uids = set(role_diff.get("added_uids", []) or [])
            for item in role_state.get("equipped_drives", []) or []:
                if item.get("uid") in added_uids and not item.get("is_changed"):
                    item["is_new"] = True
            role_state["last_diff"] = role_diff
        new_state[role_name] = role_state
    with open(state_mgr.state_file, "w", encoding="utf-8") as f:
        json.dump(new_state, f, ensure_ascii=False, indent=4)
    window.equipped_state = new_state
    window._my_role_equipment_last_diffs = last_diffs
    window._my_role_equipment_dirty_roles = set()
    if hasattr(window, "_refresh_equip"):
        window._refresh_equip()


def _flush_role_widgets(window):
    """提交当前正在编辑的角色页输入值。"""
    widget = getattr(window, "my_role_form_widget", None)
    if widget is None:
        return
    for child in widget.findChildren(NoWheelDoubleSpinBox):
        child.interpretText()
        child.clearFocus()
    for child in widget.findChildren(QLineEdit):
        child.clearFocus()


def confirm_pending_my_role_changes(window):
    """离开角色页前处理未保存修改。"""
    if not getattr(window, "_my_role_dirty", False):
        return True
    ret = QMessageBox.question(
        window,
        "未保存角色配置",
        "角色配置有未保存修改，是否先保存？\n如果本次替换过驱动，保存会同步更新已锁定配装。",
        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        QMessageBox.Save,
    )
    if ret == QMessageBox.Cancel:
        return False
    if ret == QMessageBox.Save:
        return _save_my_roles(window)
    window._my_role_dirty = False
    window._my_role_equipment_dirty_roles = set()
    window._my_role_form_data = None
    _refresh_my_role(window)
    return True


def _render_my_roles(window):
    # 记录当前选中的角色（用于删除/添加后保持选中）
    current_role = getattr(window, '_current_my_role', None)

    """清除旧内容并重新渲染所有角色，分块展示各模块."""
    layout = window.my_role_form_layout
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    # 加载数据并自动合并模型中的新角色
    data = merge_new_roles_from_model()
    window._my_role_form_data = data
    window._my_role_dirty = False
    window._my_role_equipment_dirty_roles = set()

    if not data:
        layout.addWidget(QLabel("暂无角色数据，请确保 my_roles.json 或 my_roles_model.json 存在。"))
        return

    # ----- 加载角色顺序（从独立文件） -----
    order = load_role_order()
    valid_order = [name for name in order if name in data]
    missing = sorted(set(data.keys()) - set(valid_order))
    valid_order.extend(missing)
    save_role_order(valid_order)  # 确保文件同步
    all_names = valid_order

    header = QHBoxLayout()
    role_search = QLineEdit()
    role_search.setPlaceholderText("搜索角色（支持拼音）...")
    role_search.setClearButtonEnabled(True)
    header.addWidget(role_search)
    header.addStretch()
    layout.addLayout(header)

    tabs = QTabWidget()
    tab_indices = {}

    def filter_tabs(filter_text=""):
        keyword = filter_text.strip()
        for role_name, index in tab_indices.items():
            visible = match_pinyin(role_name, keyword) if keyword else True
            tabs.setTabVisible(index, visible)

    # ------------------------------------------------------------
    # 内部辅助：为字典生成一组行（数值型）
    def add_dict_rows(parent_layout, data_dict, path_prefix, window, role_name):
        def safe_float(v):
            try:
                if v is None or v == "":
                    return 0.0
                return float(v)
            except Exception:
                return 0.0

        for key, val in data_dict.items():
            row = QHBoxLayout()
            row.addWidget(QLabel(key))

            spin = NoWheelDoubleSpinBox()
            spin.setRange(-999999, 999999)
            spin.setDecimals(2)

            spin.setValue(safe_float(val))

            spin.editingFinished.connect(
                lambda rn=role_name, p=path_prefix, k=key, s=spin:
                _update_nested_field(window, rn, p + [k], s.value())
            )

            row.addWidget(spin)
            row.addStretch()
            parent_layout.addLayout(row)

    # 内部辅助：生成一个带有标签和数值输入框的单行
    def add_single_value_row(parent_layout, label_text, path, window, role_name, default=0.0, is_float=True,
                             is_str=False):
        row = QHBoxLayout()
        row.addWidget(QLabel(label_text))
        if is_str:
            widget = QLineEdit()
            widget.setText(str(default))
            widget.editingFinished.connect(
                lambda rn=role_name, p=path, w=widget:
                _update_nested_field(window, rn, p, w.text())
            )
        else:
            widget = NoWheelDoubleSpinBox()
            widget.setRange(-999999, 999999)
            widget.setDecimals(2 if is_float else 0)
            widget.setValue(float(default))
            widget.editingFinished.connect(
                lambda rn=role_name, p=path, w=widget:
                _update_nested_field(window, rn, p, w.value() if is_float else int(w.value()))
            )
        row.addWidget(widget)
        row.addStretch()
        parent_layout.addLayout(row)

    # ------------------------------------------------------------
    def _apply_margins_to_weights(rn, margins):
        """将边际收益的 gain 值覆盖到对应权重"""
        if rn not in data:
            return
        stats_config = load_stats()
        alias_map = stats_config.get("benefit_alias_mapping", {})
        weights = data[rn].setdefault("weights", {})

        updated = apply_margins_to_weights(weights, margins, alias_map)

        if updated == 0:
            QMessageBox.information(window, "提示", "当前权重中没有与边际收益匹配的词条，未能更新。")
        else:
            _mark_my_role_dirty(window)
            refresh_weight_group(window, rn)

    def populate_role_tab(role_name, tab_scroll):
        role_name = str(role_name)  # 确保是字符串
        if tab_scroll.property("loaded"):
            return
        tab_scroll.setProperty("loaded", True)
        tab_widget = QWidget()
        tab_scroll.setWidget(tab_widget)
        form = QVBoxLayout(tab_widget)
        form.setSpacing(15)
        form.setContentsMargins(15, 15, 15, 15)

        role_data = data[role_name]  # 从 data 获取

        # ---- 0. 边际收益 ----
        def _refresh_weight_block():
            refresh_weight_group(window, role_name)

        margin_panel = MarginalBenefitPanel(
            parent_layout=form,
            window=window,
            role_name=role_name,
            role_data=role_data,
            on_weight_changed_callback=_refresh_weight_block,
        )

        if not hasattr(window, "_margin_panels"):
            window._margin_panels = {}
        window._margin_panels[role_name] = margin_panel

        # ---- 1. 驱动加成 ----
        def _refresh_margin_panel_for_role():
            if hasattr(window, "_margin_panels"):
                panel = window._margin_panels.get(role_name)
                if panel:
                    panel.refresh()

        def _refresh_drive_block():
            from .drive_widget import refresh_drive_group
            refresh_drive_group(window, role_name)

        def _on_show_drive_details():
            show_drive_details(
                window,
                role_name,
                save_callback=lambda: _mark_my_role_dirty(window),
                refresh_callback=None,
                refresh_margin_callback=_refresh_margin_panel_for_role,
                refresh_drive_callback=_refresh_drive_block,
            )

        drive_group = build_drive_group(
            parent_layout=form,
            window=window,
            role_name=role_name,
            role_data=role_data,
            on_details_callback=_on_show_drive_details,
        )

        if not hasattr(window, "_drive_groups"):
            window._drive_groups = {}
        window._drive_groups[role_name] = drive_group

        # ---- 2. 基础加成 ----
        def _refresh_margin_panel_for_role():
            if hasattr(window, "_margin_panels"):
                panel = window._margin_panels.get(role_name)
                if panel:
                    panel.refresh()

        def _on_base_data_changed():
            _mark_my_role_dirty(window)
            _refresh_margin_panel_for_role()

        base_widget = BaseStatsWidget(
            parent_layout=form,
            window=window,
            role_name=role_name,
            role_data=role_data,
            on_data_changed_callback=_on_base_data_changed,
            on_level_changed_callback=_refresh_margin_panel_for_role,
        )
        if not hasattr(window, "_base_widgets"):
            window._base_widgets = {}
        window._base_widgets[role_name] = base_widget

        # ---- 3. 弧盘加成 ----
        def _refresh_margin_panel_for_role():
            if hasattr(window, "_margin_panels"):
                panel = window._margin_panels.get(role_name)
                if panel:
                    panel.refresh()

        weapon_group = build_weapon_group(
            parent_layout=form,
            window=window,
            role_name=role_name,
            role_data=role_data,
            on_save_callback=lambda: _mark_my_role_dirty(window),
            on_margin_refresh_callback=_refresh_margin_panel_for_role,
        )

        # 存储引用以便刷新
        if not hasattr(window, "_weapon_groups"):
            window._weapon_groups = {}
        window._weapon_groups[role_name] = weapon_group

        # ---- 4. 空幕加成 ----
        def _refresh_margin_panel_for_role():
            if hasattr(window, "_margin_panels"):
                panel = window._margin_panels.get(role_name)
                if panel:
                    panel.refresh()

        tape_group = build_tape_group(
            parent_layout=form,
            window=window,
            role_name=role_name,
            role_data=role_data,
            on_save_callback=lambda: _mark_my_role_dirty(window),
            on_margin_refresh_callback=_refresh_margin_panel_for_role,
            on_update_nested_field=_update_nested_field,
        )

        # 存储引用以便刷新
        if not hasattr(window, "_tape_groups"):
            window._tape_groups = {}
        window._tape_groups[role_name] = tape_group

        # ---- 5. 词条权重 ----
        def _refresh_margin_panel_for_role():
            if hasattr(window, "_margin_panels"):
                panel = window._margin_panels.get(role_name)
                if panel:
                    panel.refresh()

        def _refresh_weight_block():
            refresh_weight_group(window, role_name)

        weight_group = build_weight_group(
            parent_layout=form,
            window=window,
            role_name=role_name,
            role_data=role_data,
            on_save_callback=lambda: _mark_my_role_dirty(window),
            on_margin_refresh_callback=_refresh_margin_panel_for_role,
        )

        # 存储引用以便刷新
        if not hasattr(window, "_weight_groups"):
            window._weight_groups = {}
        window._weight_groups[role_name] = weight_group

        form.addSpacing(100)  # 添加100像素固定空白
        form.addStretch()  # 添加弹性空间，使内容顶部对齐
        form.addStretch()

    # 构建标签页
    def rebuild_all_tabs():
        nonlocal all_names
        while tabs.count():
            tabs.removeTab(0)
        tab_indices.clear()
        for rname in all_names:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setProperty("loaded", False)
            idx = tabs.addTab(scroll, rname)
            tab_indices[rname] = idx
        filter_tabs(role_search.text())
        tabs.currentChanged.connect(lambda idx: _on_tab_changed(idx))
        # 恢复之前选中的角色
        if current_role in tab_indices:
            tabs.setCurrentIndex(tab_indices[current_role])
        _load_visible_tab()
        # 启用拖拽并保存顺序到独立文件
        tabs.setMovable(True)

        def on_tab_moved(from_idx, to_idx):
            new_order = [tabs.tabText(i) for i in range(tabs.count())]
            save_role_order(new_order)

        tabs.tabBar().tabMoved.connect(on_tab_moved)

    def _on_tab_changed(index):
        if index >= 0:
            window._current_my_role = tabs.tabText(index)
            _load_visible_tab()

    def _load_visible_tab():
        idx = tabs.currentIndex()
        if idx < 0:
            return
        rname = tabs.tabText(idx)
        if rname in data:
            populate_role_tab(rname, tabs.widget(idx))

    rebuild_all_tabs()
    role_search.textChanged.connect(filter_tabs)
    layout.addWidget(tabs)


def _update_field(window, role_name, key, value):
    """更新角色顶层字段."""
    data = window._my_role_form_data
    if data is None:
        return
    data[role_name][key] = value
    _mark_my_role_dirty(window)


def _update_info_field(window, role_name, key, value):
    """更新 sub_stats 子字段."""
    data = window._my_role_form_data
    if data is None:
        return
    data[role_name].setdefault("sub_stats", {})[key] = value
    _mark_my_role_dirty(window)


def _update_nested_field(window, role_name, path, value):
    """根据路径列表更新嵌套字段."""
    data = window._my_role_form_data
    if data is None:
        return
    obj = data[role_name]
    for key in path[:-1]:
        obj = obj.setdefault(key, {})
    obj[path[-1]] = value
    _mark_my_role_dirty(window)
