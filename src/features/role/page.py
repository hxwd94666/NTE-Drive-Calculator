# src/features/role/page.py
"""角色详情编辑页面 (my_roles.json)."""

from __future__ import annotations

import re

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
from src.ui.puzzle_board import PuzzleBoardWidget
from src.ui.widgets import NoWheelDoubleSpinBox, SearchableComboBox, match_pinyin

from .paths import get_roles_img_path
from .dao import (
    load_my_roles,
    save_my_roles,
    load_role_order,
    save_role_order,
    load_stats,
    load_weapons,
    load_tapes,
    load_real_inventory,
)
from .core import (
    calc_drive_bonus_stats,
    apply_margins_to_weights,
)
from .marginal_widget import MarginalBenefitPanel
from .base_widget import BaseStatsWidget
from .drive_widget import build_drive_group, show_drive_details
from .weapon_widget import build_weapon_group, refresh_weapon_group
from .tape_widget import build_tape_group, refresh_tape_group
from .weight_widget import build_weight_group, refresh_weight_group

__all__ = ["_page_my_role", "_refresh_my_role", "install_methods"]


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
    save_btn = QPushButton("保存")
    save_btn.setObjectName("btnPrimary")

    def _flush_role_widgets(widget: QWidget):
        """强制提交所有 spinbox / input 当前值"""
        for child in widget.findChildren(NoWheelDoubleSpinBox):
            child.interpretText()
            child.clearFocus()

        for child in widget.findChildren(QLineEdit):
            child.clearFocus()

    def _on_save():
        _flush_role_widgets(window.my_role_form_widget)
        _save_my_roles(window)

    save_btn.clicked.connect(_on_save)
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
    data = getattr(window, "_my_role_form_data", None)
    if data is None:
        QMessageBox.information(window, "提示", "没有需要保存的数据。")
        return
    if save_my_roles(data):
        window._my_role_dirty = False
        QMessageBox.information(window, "保存", "my_roles.json 已保存")
        # 刷新界面
        _refresh_my_role(window)
    else:
        QMessageBox.warning(window, "保存失败", "保存 my_roles.json 失败")


def _save_my_roles_silent(window):
    """静默保存，不弹提示框"""
    data = getattr(window, "_my_role_form_data", None)
    if data is None:
        return
    save_my_roles(data)
    window._my_role_dirty = False


def _mark_my_role_dirty(window):
    """标记角色页面有未保存修改."""
    window._my_role_dirty = True


def _render_my_roles(window):
    # 记录当前选中的角色（用于删除/添加后保持选中）
    current_role = getattr(window, '_current_my_role', None)

    """清除旧内容并重新渲染所有角色，分块展示各模块."""
    layout = window.my_role_form_layout
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()

    data = load_my_roles()
    window._my_role_form_data = data
    window._my_role_dirty = False

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
            _save_my_roles_silent(window)
            _refresh_my_role(window)

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

        # ---- 1. 基础加成 ----
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

        # ---- 2. 驱动加成 ----
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
                save_callback=lambda: _save_my_roles_silent(window),
                refresh_callback=None,  # 不再需要完全刷新页面
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

        # 存储驱动组引用以便刷新
        if not hasattr(window, "_drive_groups"):
            window._drive_groups = {}
        window._drive_groups[role_name] = drive_group

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
            on_save_callback=lambda: _save_my_roles_silent(window),
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
            on_save_callback=lambda: _save_my_roles_silent(window),
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
            on_save_callback=lambda: _save_my_roles_silent(window),
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