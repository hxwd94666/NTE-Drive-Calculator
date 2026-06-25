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
        # 创建边际收益面板
        margin_panel = MarginalBenefitPanel(
            parent_layout=form,
            window=window,
            role_name=role_name,
            role_data=role_data
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
        def build_weapon_group(window, role_name, role_data, form):
            group_weapon = QGroupBox("弧盘加成")
            weapon_layout = QVBoxLayout(group_weapon)
            weapon_layout.setSpacing(8)

            group_weapon = QGroupBox("弧盘加成")
            weapon_layout = QVBoxLayout(group_weapon)
            weapon_layout.setSpacing(8)

            stats = load_stats()
            tape_pool = stats.get("tape_stat_values", {})
            tape_main_pool_value = stats.get("tape_main_stat_values", {})

            weapon_data = role_data.get("weapon")
            if not isinstance(weapon_data, dict):
                weapon_data = {}
                role_data["weapon"] = weapon_data

            weapon_data.setdefault("name", "")
            weapon_data.setdefault("sub_stats", {})  # 基础加成（攻击力白值等）
            weapon_data.setdefault("skill", {})  # 技能对象
            skill_obj = weapon_data["skill"]
            skill_obj.setdefault("sub_stats", {})  # 技能基础加成（可多行）
            skill_obj.setdefault("skill", {})  # 技能具体加成（可多行）
            skill_obj.setdefault("skill_cover", 0.8)  # 技能覆盖率

            # =========================
            # 1. 名称行（带选取按钮）
            # =========================
            name_row = QHBoxLayout()
            name_row.addWidget(QLabel("名称:"))

            name_edit = QLineEdit()
            name_edit.setText(weapon_data.get("name", ""))
            name_edit.textChanged.connect(lambda: _save_my_roles_silent(window))
            name_row.addWidget(name_edit)

            def load_weapon_data():
                weapon_db = load_weapons()
                names = list(weapon_db.keys())
                if not names:
                    QMessageBox.information(window, "提示", "weapon.json 中没有弧盘数据")
                    return

                # 1. 选择弧盘
                selected, ok = QInputDialog.getItem(window, "选择弧盘", "请选择弧盘：", names, 0, False)
                if not ok or not selected:
                    return

                weapon_info = weapon_db[selected]

                # 2. 选择混频等级（mix_level）
                mix_levels = weapon_info.get("mix_level_sub_stats", {})
                if mix_levels:
                    # 提取等级键（可能为字符串 "1","2","3","4","5"）
                    level_keys = sorted(mix_levels.keys(), key=lambda x: int(x) if x.isdigit() else 0)
                    if level_keys:
                        level, ok = QInputDialog.getItem(window, "选择混频等级", "请选择混频等级（1~5）：", level_keys, 0, False)
                        if not ok or not level:
                            return
                        selected_mix = mix_levels[level]
                    else:
                        # 没有有效等级，使用空
                        selected_mix = {}
                else:
                    # 没有 mix_level_sub_stats，使用空
                    selected_mix = {}

                # ---- 更新数据 ----
                weapon_data["name"] = selected

                # 基础加成（weapon.sub_stats）
                if "sub_stats" in weapon_info and isinstance(weapon_info["sub_stats"], dict):
                    weapon_data["sub_stats"] = weapon_info["sub_stats"].copy()
                else:
                    weapon_data["sub_stats"] = {}

                # 技能部分（weapon.skill）
                skill_obj = weapon_data["skill"]
                skill_obj["sub_stats"] = selected_mix.get("sub_stats", {}).copy()
                skill_obj["skill"] = selected_mix.get("skill", {}).copy()
                skill_obj["skill_cover"] = float(selected_mix.get("skill_cover", 0.8))

                # ---- 重新渲染 ----
                layout = form.layout()
                old_group = window._weapon_group
                if old_group:
                    index = layout.indexOf(old_group)  # 获取索引
                    if index >= 0:
                        layout.removeWidget(old_group)
                        old_group.deleteLater()
                        new_group = build_weapon_group(window, role_name, role_data, form)
                        layout.insertWidget(index, new_group)  # 插入到相同位置
                        window._weapon_group = new_group
                    else:
                        # 如果找不到，直接添加
                        old_group.deleteLater()
                        new_group = build_weapon_group(window, role_name, role_data, form)
                        layout.addWidget(new_group)
                        window._weapon_group = new_group
                else:
                    new_group = build_weapon_group(window, role_name, role_data, form)
                    layout.addWidget(new_group)
                    window._weapon_group = new_group

            # 按钮
            select_btn = QPushButton("选取弧盘")
            select_btn.setObjectName("btnAction")
            select_btn.clicked.connect(load_weapon_data)
            name_row.addWidget(select_btn)

            weapon_layout.addLayout(name_row)

            # =========================
            # 2. 基础加成（对应 weapon.sub_stats）
            # =========================
            base_label = QLabel("基础加成：")
            base_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
            weapon_layout.addWidget(base_label)

            base_info = weapon_data["sub_stats"]
            base_info.setdefault("攻击力白值", 300.0)

            def safe_float(v):
                try:
                    return float(v) if v not in (None, "") else 0.0
                except:
                    return 0.0

            # 提取现有非“攻击力白值”的键（最多两个）
            existing_keys = [k for k in base_info.keys() if k != "攻击力白值"]
            second_key = existing_keys[0] if len(existing_keys) >= 1 else None
            third_key = existing_keys[1] if len(existing_keys) >= 2 else None

            # --- 攻击力白值 ---
            white_spin = NoWheelDoubleSpinBox()
            white_spin.setRange(-999999, 999999)
            white_spin.setValue(float(base_info.get("攻击力白值", 300.0)))
            row1 = QHBoxLayout()
            row1.addWidget(QLabel("攻击力白值"))
            row1.addWidget(white_spin)
            weapon_layout.addLayout(row1)

            # --- 第二个属性（基础属性）---
            combo2 = SearchableComboBox()
            combo2.addItem("")
            combo2.addItems(list(tape_pool.keys()))
            if second_key and second_key in tape_pool:
                combo2.setCurrentText(second_key)
            else:
                combo2.setCurrentIndex(0)
            spin2 = NoWheelDoubleSpinBox()
            spin2.setRange(-999999, 999999)
            spin2.setValue(safe_float(base_info.get(second_key, 0.0)) if second_key else 0.0)
            row2 = QHBoxLayout()
            row2.addWidget(QLabel("基础属性"))
            row2.addWidget(combo2)
            row2.addWidget(spin2)
            weapon_layout.addLayout(row2)

            def commit_base():
                new_base = {"攻击力白值": white_spin.value()}
                k2 = combo2.currentText().strip()
                if k2 and k2 in tape_pool:
                    new_base[k2] = spin2.value()
                weapon_data["sub_stats"] = new_base
                _save_my_roles_silent(window)

            white_spin.editingFinished.connect(commit_base)
            combo2.currentTextChanged.connect(lambda _: commit_base())
            spin2.editingFinished.connect(commit_base)

            # =========================
            # 3. 技能加成（对应 weapon.skill）
            # =========================
            skill_label = QLabel("技能加成：")
            skill_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
            weapon_layout.addWidget(skill_label)

            # ---------- 3.1 技能基础加成（skill.sub_stats）多行 ----------
            sb_label = QLabel("技能基础加成：")
            sb_label.setStyleSheet("font-weight:bold; color:#8bc34a;")
            weapon_layout.addWidget(sb_label)

            sb_container = QWidget()
            sb_layout = QVBoxLayout(sb_container)
            sb_layout.setContentsMargins(0, 0, 0, 0)
            weapon_layout.addWidget(sb_container)

            sb_rows = []  # (combo, spin, widget)

            def add_sb_row(key="", value=0.0):
                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)

                combo = SearchableComboBox()
                combo.addItem("")
                combo.addItems(list(tape_main_pool_value.keys()))
                if key and key in tape_main_pool_value:
                    combo.setCurrentText(key)
                else:
                    combo.setCurrentIndex(0)

                spin = NoWheelDoubleSpinBox()
                spin.setRange(-999999, 999999)
                spin.setDecimals(2)
                spin.setValue(safe_float(value))

                del_btn = QPushButton("✕")  # 使用 Unicode 乘号（U+2715）
                del_btn.setFixedSize(28, 28)  # 稍微放大
                del_btn.setStyleSheet("""
                            QPushButton {
                                color: red;
                                font-weight: bold;
                                font-size: 20px;
                                min-width: 28px;
                                min-height: 28px;
                                border: none;
                                background: transparent;
                            }
                            QPushButton:hover {
                                background: #ffcccc;
                                border-radius: 4px;
                            }
                        """)
                del_btn.setFont(QFont("Arial", 14))  # 显式设置字体

                row_layout.addWidget(QLabel("属性"))
                row_layout.addWidget(combo)
                row_layout.addWidget(spin)
                row_layout.addWidget(del_btn)

                sb_layout.addWidget(row_widget)
                sb_rows.append((combo, spin, row_widget))

                def remove_row():
                    if (combo, spin, row_widget) in sb_rows:
                        sb_layout.removeWidget(row_widget)
                        row_widget.deleteLater()
                        sb_rows.remove((combo, spin, row_widget))
                        commit_sb_all()

                del_btn.clicked.connect(remove_row)

                def commit_sb_one():
                    commit_sb_all()

                combo.currentTextChanged.connect(lambda _: commit_sb_one())
                spin.editingFinished.connect(commit_sb_one)

                return row_widget

            def commit_sb_all():
                new_dict = {}
                for combo, spin, _ in sb_rows:
                    k = combo.currentText().strip()
                    if k and k in tape_pool:
                        new_dict[k] = spin.value()
                skill_obj["sub_stats"] = new_dict
                _save_my_roles_silent(window)

            for k, v in skill_obj["sub_stats"].items():
                add_sb_row(k, v)

            sb_add_btn = QPushButton("+ 添加技能基础加成")
            sb_add_btn.clicked.connect(lambda: add_sb_row())
            weapon_layout.addWidget(sb_add_btn)

            # ---------- 3.2 技能具体加成（skill.skill）多行，使用文本框 ----------
            ss_label = QLabel("技能具体加成：")
            ss_label.setStyleSheet("font-weight:bold; color:#8bc34a;")
            weapon_layout.addWidget(ss_label)

            ss_container = QWidget()
            ss_layout = QVBoxLayout(ss_container)
            ss_layout.setContentsMargins(0, 0, 0, 0)
            weapon_layout.addWidget(ss_container)

            ss_rows = []  # (line_edit, spin, widget)

            def add_ss_row(key="", value=0.0):
                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)

                # 可编辑文本框
                line_edit = QLineEdit()
                line_edit.setText(key)
                line_edit.setPlaceholderText("输入属性名")

                spin = NoWheelDoubleSpinBox()
                spin.setRange(-999999, 999999)
                spin.setDecimals(2)
                spin.setValue(safe_float(value))

                del_btn = QPushButton("✕")  # 使用 Unicode 乘号（U+2715）
                del_btn.setFixedSize(28, 28)  # 稍微放大
                del_btn.setStyleSheet("""
                            QPushButton {
                                color: red;
                                font-weight: bold;
                                font-size: 20px;
                                min-width: 28px;
                                min-height: 28px;
                                border: none;
                                background: transparent;
                            }
                            QPushButton:hover {
                                background: #ffcccc;
                                border-radius: 4px;
                            }
                        """)
                del_btn.setFont(QFont("Arial", 14))  # 显式设置字体

                row_layout.addWidget(QLabel("属性"))
                row_layout.addWidget(line_edit)
                row_layout.addWidget(spin)
                row_layout.addWidget(del_btn)

                ss_layout.addWidget(row_widget)
                ss_rows.append((line_edit, spin, row_widget))

                def remove_row():
                    if (line_edit, spin, row_widget) in ss_rows:
                        ss_layout.removeWidget(row_widget)
                        row_widget.deleteLater()
                        ss_rows.remove((line_edit, spin, row_widget))
                        commit_ss_all()

                del_btn.clicked.connect(remove_row)

                def commit_ss_one():
                    commit_ss_all()

                line_edit.textChanged.connect(lambda _: commit_ss_one())
                spin.editingFinished.connect(commit_ss_one)

                return row_widget

            def commit_ss_all():
                new_dict = {}
                for line_edit, spin, _ in ss_rows:
                    k = line_edit.text().strip()
                    if k:  # 只要非空就保存，不限制必须来自tape_pool
                        new_dict[k] = spin.value()
                skill_obj["skill"] = new_dict
                _save_my_roles_silent(window)

            # 加载已有数据
            for k, v in skill_obj["skill"].items():
                add_ss_row(k, v)

            ss_add_btn = QPushButton("+ 添加技能具体加成")
            ss_add_btn.clicked.connect(lambda: add_ss_row())
            weapon_layout.addWidget(ss_add_btn)

            # ---------- 3.3 技能覆盖率（skill_cover）----------
            cover_spin = NoWheelDoubleSpinBox()
            cover_spin.setRange(0, 1.0)
            cover_spin.setSingleStep(0.05)
            cover_spin.setDecimals(2)
            cover_spin.setValue(float(skill_obj.get("skill_cover", 0.8)))

            def commit_cover():
                skill_obj["skill_cover"] = cover_spin.value()
                _save_my_roles_silent(window)

            cover_spin.editingFinished.connect(commit_cover)

            row_cover = QHBoxLayout()
            row_cover.addWidget(QLabel("技能覆盖率"))
            row_cover.addWidget(cover_spin)
            weapon_layout.addLayout(row_cover)

            return group_weapon

        group_weapon = build_weapon_group(window, role_name, role_data, form)
        form.addWidget(group_weapon)
        window._weapon_group = group_weapon

        # ---- 4. 空幕加成 ----
        def build_tape_group(window, role_name, role_data, form):
            group_tape = QGroupBox("空幕加成")
            tape_layout = QVBoxLayout(group_tape)
            tape_layout.setSpacing(8)

            stats = load_stats()
            tape_pool = stats.get("tape_stat_values", {})
            tape_main_pool = stats.get("tape_main_stat_values", {})

            tape_data = role_data.get("tape")
            if not isinstance(tape_data, dict):
                tape_data = {}
                role_data["tape"] = tape_data

            # ---------- 固定/自动字段 ----------
            tape_data["shape_id"] = "TAPE_15"
            tape_data["quality"] = "Gold"
            if not tape_data.get("uid"):
                import time, random
                tape_data["uid"] = f"tape_{int(time.time())}_{random.randint(1000, 9999)}"

            tape_data.setdefault("display_name", "空幕")
            tape_data.setdefault("sub_stats", {})
            tape_data.setdefault("main_stats", {})
            tape_data.setdefault("skill", {})
            tape_data.setdefault("skill_2", {})
            tape_data.setdefault("skill_cover", 0.8)

            for k in ("main_stats", "skill", "skill_2", "sub_stats"):
                if not isinstance(tape_data.get(k), dict):
                    tape_data[k] = {}

            # =========================
            # 显示名 + 选取按钮
            # =========================
            name_row = QHBoxLayout()
            name_row.addWidget(QLabel("显示名:"))

            name_edit = QLineEdit()
            name_edit.setText(tape_data.get("display_name", "空幕"))
            name_edit.editingFinished.connect(
                lambda: _update_nested_field(window, role_name, ["tape", "display_name"], name_edit.text())
            )
            name_row.addWidget(name_edit)

            # ---------- 选取空幕按钮 ----------
            def load_tape_data():
                tapes_db = load_tapes()
                names = list(tapes_db.keys())
                if not names:
                    QMessageBox.information(window, "提示", "tapes.json 中没有空幕数据")
                    return

                selected, ok = QInputDialog.getItem(window, "选择空幕", "请选择空幕：", names, 0, False)
                if not ok or not selected:
                    return

                tape_info = tapes_db[selected]

                # ---- 更新数据（保留 main_stats 和 sub_stats） ----
                # 显示名：优先使用内部 display_name，否则用键名
                new_display = tape_info.get("display_name", selected)
                tape_data["display_name"] = new_display
                name_edit.setText(new_display)

                # 技能1（只取第一个键值对，如果存在多个则只保留第一个）
                if "skill" in tape_info and isinstance(tape_info["skill"], dict) and tape_info["skill"]:
                    first_key = next(iter(tape_info["skill"]))
                    tape_data["skill"] = {first_key: tape_info["skill"][first_key]}
                else:
                    tape_data["skill"] = {}

                # 技能2（同样只取第一个）
                if "skill_2" in tape_info and isinstance(tape_info["skill_2"], dict) and tape_info["skill_2"]:
                    first_key = next(iter(tape_info["skill_2"]))
                    tape_data["skill_2"] = {first_key: tape_info["skill_2"][first_key]}
                else:
                    tape_data["skill_2"] = {}

                # 覆盖率
                tape_data["skill_cover"] = float(tape_info.get("skill_cover", 0.8))

                # ---- 重绘 ----
                layout = form.layout()
                old_group = window._tape_group
                if old_group:
                    # 在布局中查找旧组的位置
                    idx = layout.indexOf(old_group)  # 适用于 QFormLayout 或 QVBoxLayout
                    if idx >= 0:
                        layout.removeWidget(old_group)
                        old_group.deleteLater()
                        new_group = build_tape_group(window, role_name, role_data, form)
                        layout.insertWidget(idx, new_group)
                        window._tape_group = new_group
                    else:
                        # 如果找不到，直接删除并添加到最后
                        old_group.deleteLater()
                        new_group = build_tape_group(window, role_name, role_data, form)
                        form.addWidget(new_group)
                        window._tape_group = new_group
                else:
                    new_group = build_tape_group(window, role_name, role_data, form)
                    form.addWidget(new_group)
                    window._tape_group = new_group

            select_btn = QPushButton("选取空幕")
            select_btn.setObjectName("btnAction")
            select_btn.clicked.connect(load_tape_data)
            name_row.addWidget(select_btn)

            tape_layout.addLayout(name_row)

            # =========================
            # 主词条
            # =========================
            # ... 原有的主词条 UI 代码保持不变 ...
            main_label = QLabel("主词条（属性名 + 数值）：")
            main_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
            tape_layout.addWidget(main_label)

            main_row = QHBoxLayout()
            main_keys = list(tape_main_pool.keys()) if tape_main_pool else ["攻击力%"]
            main_combo = SearchableComboBox()
            main_combo.addItem("")
            main_combo.addItems(main_keys)

            main_spin = NoWheelDoubleSpinBox()
            main_spin.setRange(-999999, 999999)
            main_spin.setDecimals(2)

            saved_main = tape_data.get("main_stats", {})
            if saved_main and isinstance(saved_main, dict):
                saved_key = next(iter(saved_main.keys()), "")
                saved_val = saved_main.get(saved_key, 0.0)
                if saved_key in main_keys:
                    main_combo.setCurrentText(saved_key)
                else:
                    main_combo.setCurrentIndex(0)
                main_spin.setValue(float(saved_val))
            else:
                main_combo.setCurrentIndex(0)
                main_spin.setValue(0.0)

            def commit_main():
                key = main_combo.currentText().strip()
                val = main_spin.value()
                if key and key in main_keys:
                    tape_data["main_stats"] = {key: val}
                else:
                    tape_data["main_stats"] = {}
                _save_my_roles_silent(window)

            main_combo.currentTextChanged.connect(lambda _: commit_main())
            main_spin.editingFinished.connect(commit_main)

            main_row.addWidget(main_combo)
            main_row.addWidget(main_spin)
            tape_layout.addLayout(main_row)

            # =========================
            # 副属性（最多4条）
            # =========================
            sub_label = QLabel("副属性（最多4条，留空表示未设置）：")
            sub_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
            tape_layout.addWidget(sub_label)

            sub_entries = list(tape_data.get("sub_stats", {}).items())
            while len(sub_entries) < 4:
                sub_entries.append(("", 0.0))
            sub_entries = sub_entries[:4]

            tape_pool_items = [f"{k} ({v})" for k, v in tape_pool.items()]
            sub_widgets = []

            for idx, (key, val) in enumerate(sub_entries):
                row = QHBoxLayout()
                combo = SearchableComboBox()
                combo.addItem("")
                combo.addItems(tape_pool_items)
                if key and key in tape_pool:
                    combo.setCurrentText(f"{key} ({tape_pool[key]})")
                else:
                    combo.setCurrentIndex(0)

                spin = NoWheelDoubleSpinBox()
                spin.setRange(-999999, 999999)
                spin.setDecimals(2)
                spin.setValue(float(val))

                def update_spin_from_combo(cb, sp):
                    text = cb.currentText()
                    if text:
                        k = text.rsplit(" (", 1)[0]
                        v = tape_pool.get(k, 0.0)
                        sp.setValue(v)
                    else:
                        sp.setValue(0.0)

                combo.currentTextChanged.connect(
                    lambda _, c=combo, sp=spin: update_spin_from_combo(c, sp)
                )

                row.addWidget(combo)
                row.addWidget(spin)
                tape_layout.addLayout(row)
                sub_widgets.append((combo, spin))

            def commit_sub_stats():
                new_sub = {}
                for combo, spin in sub_widgets:
                    text = combo.currentText().strip()
                    if text:
                        key = text.rsplit(" (", 1)[0]
                        val = spin.value()
                        new_sub[key] = val
                tape_data["sub_stats"] = new_sub
                _save_my_roles_silent(window)

            for combo, spin in sub_widgets:
                combo.currentTextChanged.connect(lambda _, c=combo, sp=spin: commit_sub_stats())
                spin.editingFinished.connect(commit_sub_stats)

            # =========================
            # skill_cover
            # =========================
            add_single_value_row(
                tape_layout,
                "技能2覆盖率:",
                ["tape", "skill_cover"],
                window,
                role_name,
                default=float(tape_data.get("skill_cover", 0.8)),
                is_float=True
            )

            # =========================
            # 技能 skill 和 skill_2
            # =========================
            if not tape_pool:
                tape_pool = {"攻击力%": 0}

            tape_skill = tape_data["skill"]
            tape_skill2 = tape_data["skill_2"]

            def normalize_skill_dict(d, pool):
                if not d:
                    d[next(iter(pool.keys()))] = 0.0
                    return
                if len(d) > 1:
                    k = next(iter(d))
                    v = d[k]
                    d.clear()
                    d[k] = v
                k = next(iter(d))
                if k not in pool:
                    pool[k] = 0.0

            normalize_skill_dict(tape_skill, tape_pool)
            normalize_skill_dict(tape_skill2, tape_pool)

            def create_single_skill_row(skill_dict, label_text):
                row_label = QLabel(label_text)
                row_label.setStyleSheet("color:#aaa;")
                tape_layout.addWidget(row_label)

                row = QHBoxLayout()
                key = next(iter(skill_dict.keys()))
                val = float(skill_dict[key])

                combo = SearchableComboBox()
                combo.addItems(list(tape_pool.keys()))
                combo.setCurrentText(key)

                spin = NoWheelDoubleSpinBox()
                spin.setRange(-999999, 999999)
                spin.setDecimals(2)
                spin.setValue(val)

                def commit():
                    k = combo.currentText()
                    v = spin.value()
                    skill_dict.clear()
                    skill_dict[k] = v
                    _save_my_roles_silent(window)

                combo.currentTextChanged.connect(lambda _: commit())
                spin.editingFinished.connect(commit)

                row.addWidget(combo)
                row.addWidget(spin)
                tape_layout.addLayout(row)

            create_single_skill_row(tape_skill, "技能1：")
            create_single_skill_row(tape_skill2, "技能2：")

            return group_tape

        group_tape = build_tape_group(window, role_name, role_data, form)
        form.addWidget(group_tape)
        window._tape_group = group_tape

        # ---- 5. 词条权重 ----
        group_weights = QGroupBox("词条权重")
        weights_layout = QVBoxLayout(group_weights)
        weights_layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("词条权重:"))
        top_row.addStretch()
        add_btn = QPushButton("+ 添加词条")
        add_btn.setObjectName("btnAction")
        top_row.addWidget(add_btn)
        weights_layout.addLayout(top_row)

        weights_container = QVBoxLayout()
        weights_layout.addLayout(weights_container)

        weights_dict = role_data.get("weights", {})

        # 渲染权重行
        for key in sorted(weights_dict.keys()):
            val = weights_dict[key]
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(QLabel(key))

            spin = NoWheelDoubleSpinBox()
            spin.setRange(0, 10)
            spin.setSingleStep(0.05)
            spin.setDecimals(3)
            spin.setValue(float(val))
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(lambda v, k=key: _update_weight_value(k, v))
            row.addWidget(spin)

            del_btn = QPushButton("×")
            del_btn.setObjectName("btnSm")
            del_btn.setFixedSize(28, 28)
            del_btn.clicked.connect(lambda checked=False, rn=role_name, k=key: _delete_weight(rn, k))
            row.addWidget(del_btn)
            weights_container.addLayout(row)

        def _update_weight_value(k, v):
            if role_name in data:
                data[role_name].setdefault("weights", {})[k] = v
                _mark_my_role_dirty(window)

        def _delete_weight(rn, k):
            if rn in data and k in data[rn].get("weights", {}):
                del data[rn]["weights"][k]
                _save_my_roles_silent(window)  # 静默保存，不弹窗
                _refresh_my_role(window)  # 刷新界面

        def _add_weight():
            stats = load_stats()
            pool = sorted(stats.get("weight_pool", []))
            existing = set(weights_dict.keys())
            available = [s for s in pool if s not in existing]
            if not available:
                QMessageBox.information(window, "提示", "所有词条已添加。")
                return
            wt, ok = QInputDialog.getItem(window, "添加词条", "选择词条:", available, 0, False)
            if ok and wt.strip():
                data[role_name].setdefault("weights", {})[wt.strip()] = 0.5
                _save_my_roles_silent(window)
                _refresh_my_role(window)

        add_btn.clicked.connect(_add_weight)

        form.addWidget(group_weights)

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