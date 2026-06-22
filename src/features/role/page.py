# src/features/role/page.py
"""角色详情编辑页面 (my_roles.json)."""

from __future__ import annotations

import json
from pathlib import Path
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

from PySide6.QtNetwork import QNetworkReply
from PySide6.QtWidgets import QHeaderView
from PySide6.QtCore import Qt
from src.ui.puzzle_board import PuzzleBoardWidget
from src.ui.widgets import NoWheelDoubleSpinBox, SearchableComboBox, match_pinyin
from src.app import runtime

__all__ = ["_page_my_role", "_refresh_my_role", "install_methods"]


def install_methods(app_module, window_cls):
    """Install feature methods onto the main window class."""
    window_cls._page_my_role = _page_my_role
    window_cls._refresh_my_role = _refresh_my_role


def _get_role_order_path(window) -> Path:
    """返回 role_order.json 路径（与 my_roles.json 同目录）"""
    config_dir = _get_user_account_config_dir()
    return config_dir / "role_order.json"


def _load_role_order(window) -> list:
    """加载角色顺序列表，若文件不存在或格式错误返回空列表"""
    path = _get_role_order_path(window)
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def _save_role_order(window, order: list):
    """保存角色顺序到 role_order.json（覆盖写入）"""
    path = _get_role_order_path(window)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(order, f, ensure_ascii=False, indent=2)


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


def _get_config_dir(window) -> Path:
    """通过 settings_paths 获取当前账号的 config 目录。"""
    return window._settings_paths()["config_dir"]


def _get_user_account_config_dir() -> Path:
    return runtime.USER_CONFIG_DIR


def _get_my_roles_path() -> Path:
    config_dir = _get_user_account_config_dir()
    return config_dir / "my_roles.json"


def _get_my_roles_model_path(window) -> Path:
    config_dir = _get_config_dir(window)
    return config_dir / "my_roles_model.json"


def _get_roles_img_path(window, role_name) -> Path:
    config_dir = _get_config_dir(window)
    return config_dir / "templates" / "roles" / f"{role_name}.png"


def _get_stats_path(window) -> Path:
    config_dir = _get_config_dir(window)
    return config_dir / "stats.json"


def _get_weapon_path(window) -> Path:
    config_dir = _get_config_dir(window)
    return config_dir / "weapons.json"

def _get_tape_path(window) -> Path:
    config_dir = _get_config_dir(window)
    return config_dir / "tapes.json"


def _load_stats(window) -> dict:
    """加载 stats.json（词条配置源）"""
    filepath = _get_stats_path(window)

    if not filepath.exists():
        return {}

    with open(filepath, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}


def _load_my_roles(window) -> dict:
    """加载 my_roles.json 数据，若不存在则从模板复制."""
    filepath = _get_my_roles_path()
    model_path = _get_my_roles_model_path(window)
    if not filepath.exists() and model_path.exists():
        import shutil
        shutil.copy(model_path, filepath)
    if not filepath.exists():
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_my_roles(window):
    """保存当前编辑的数据到 my_roles.json，并刷新界面。"""
    data = getattr(window, "_my_role_form_data", None)
    if data is None:
        QMessageBox.information(window, "提示", "没有需要保存的数据。")
        return
    filepath = _get_my_roles_path()
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    window._my_role_dirty = False
    QMessageBox.information(window, "保存", "my_roles.json 已保存")
    # 刷新界面
    _refresh_my_role(window)


def _save_my_roles_silent(window):
    """静默保存，不弹提示框"""
    data = getattr(window, "_my_role_form_data", None)
    if data is None:
        return
    filepath = _get_my_roles_path()
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
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

    data = _load_my_roles(window)
    window._my_role_form_data = data
    window._my_role_dirty = False

    if not data:
        layout.addWidget(QLabel("暂无角色数据，请确保 my_roles.json 或 my_roles_model.json 存在。"))
        return

    # ----- 加载角色顺序（从独立文件） -----
    order = _load_role_order(window)
    valid_order = [name for name in order if name in data]
    missing = sorted(set(data.keys()) - set(valid_order))
    valid_order.extend(missing)
    _save_role_order(window, valid_order)  # 确保文件同步
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
    def add_single_value_row(parent_layout, label_text, path, window, role_name, default=0, is_float=True,
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
        stats_config = _load_stats(window)
        alias_map = stats_config.get("benefit_alias_mapping", {})
        weights = data[rn].setdefault("weights", {})

        # 建立反向映射：规范名 -> 所有权重键列表
        reverse_map = {}
        for wk in weights.keys():
            canonical = alias_map.get(wk, wk)
            reverse_map.setdefault(canonical, []).append(wk)

        # 遍历边际收益表，更新匹配的权重键
        updated = 0
        for name, cur_val, unit_val, gain in margins:
            if name in reverse_map:
                for wk in reverse_map[name]:
                    weights[wk] = round(gain, 4)  # 保留4位小数
                    updated += 1
            else:
                # 若没有对应词条，可选择自动添加，这里先给出提示
                pass

        if updated == 0:
            QMessageBox.information(window, "提示", "当前权重中没有与边际收益匹配的词条，未能更新。")
        else:
            _save_my_roles_silent(window)
            _refresh_my_role(window)

    def _update_base_stat(role_name, key, value):
        """单个基础属性变化时保存到 sub_stats"""
        data = window._my_role_form_data
        if not data:
            return
        role = data.get(role_name)
        if not role:
            return
        sub_stats = role.setdefault("sub_stats", {})
        sub_stats[key] = value
        _mark_my_role_dirty(window)

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

        role_data = data[role_name]

        # ---- 0. 边际收益 ----
        total_stats = _get_character_total_stats(window, role_name)
        base_damage, margins = _calc_marginal_benefits(window, total_stats)

        # 根据权重过滤边际收益词条（只过滤表格数据，不影响伤害显示）
        if margins:
            stats_config = _load_stats(window)
            alias_map = stats_config.get("benefit_alias_mapping", {})
            weights = role_data.get("weights", {})
            allowed_categories = set()
            for weight_key in weights.keys():
                canonical = alias_map.get(weight_key, weight_key)
                allowed_categories.add(canonical)
            margins = [m for m in margins if m[0] in allowed_categories]

        # 无论是否有表格，都创建面板显示伤害
        group_margin = QGroupBox("边际收益（按每单位收益排序）")
        margin_layout = QVBoxLayout(group_margin)

        # 显示总伤害（直伤评分）
        damage_label = QLabel(f"直伤评分 : {base_damage:.2f}")
        damage_label.setStyleSheet("font-weight: bold; color: #ffaa00; font-size: 14px;")
        margin_layout.addWidget(damage_label)

        if margins:
            # 表格
            table = QTableWidget()
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(["参数", "当前值", "1单位", "每单位提升"])
            table.setRowCount(len(margins))
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.verticalHeader().setVisible(False)

            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

            for i, (name, cur_val, unit_val, gain) in enumerate(margins):
                table.setItem(i, 0, QTableWidgetItem(name))
                table.setItem(i, 1, QTableWidgetItem(cur_val))
                table.setItem(i, 2, QTableWidgetItem(unit_val))
                gain_item = QTableWidgetItem(f"{gain:.4f}%")
                table.setItem(i, 3, gain_item)

            header = table.horizontalHeader()
            for col in range(4):
                header.setSectionResizeMode(col, QHeaderView.Stretch)

            header_height = header.height()
            row_height = table.verticalHeader().defaultSectionSize()
            frame = table.frameWidth() * 2
            total_height = header_height + row_height * len(margins) + frame
            table.setFixedHeight(total_height)

            margin_layout.addWidget(table)

            # 添加“设为权重”按钮
            set_weights_btn = QPushButton("设为权重")
            set_weights_btn.setObjectName("btnAction")
            set_weights_btn.clicked.connect(
                lambda: _apply_margins_to_weights(role_name, margins)
            )
            margin_layout.addWidget(set_weights_btn)

        form.addWidget(group_margin)
        # ---- 1. 基础加成 ----
        group_base = QGroupBox("基础加成")
        group_base.setStyleSheet("QGroupBox{font-weight:bold;}")
        base_layout = QVBoxLayout(group_base)
        base_layout.setSpacing(8)

        # ========== 顶部行：头像 + 等级（横向排列） ==========
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        # ---- 头像（左侧） ----
        from PySide6.QtGui import QPixmap
        avatar_path = _get_roles_img_path(window, role_name)

        if avatar_path.exists():
            pixmap = QPixmap(str(avatar_path))
            if not pixmap.isNull():
                avatar_label = QLabel()
                avatar_label.setFixedSize(80, 80)
                avatar_label.setScaledContents(True)
                avatar_label.setPixmap(pixmap)
                top_row.addWidget(avatar_label, alignment=Qt.AlignLeft)

        # ---- 等级下拉（右侧，与头像同一行） ----
        level_widget = QWidget()
        level_widget.setFixedHeight(80)  # 与头像高度对齐
        level_layout = QVBoxLayout(level_widget)
        level_layout.setContentsMargins(0, 0, 0, 0)

        level_label = QLabel("等级:")
        level_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
        level_layout.addWidget(level_label, alignment=Qt.AlignCenter)

        # 获取 level_sub_stats
        level_sub_stats = role_data.get("level_sub_stats", {})
        available_levels = sorted(level_sub_stats.keys(), key=lambda x: int(x))
        if not available_levels:
            available_levels = ["1", "20", "30", "40", "50", "60", "70", "80"]

        level_combo = QComboBox()
        level_combo.addItems(available_levels)
        current_level = str(role_data.get("level", 70))
        if current_level in available_levels:
            level_combo.setCurrentText(current_level)
        else:
            level_combo.setCurrentIndex(0)
        level_combo.setFixedWidth(80)
        level_combo.setStyleSheet("font-size:14px; padding:4px;")
        level_layout.addWidget(level_combo, alignment=Qt.AlignCenter)

        top_row.addWidget(level_widget)
        top_row.addStretch()
        base_layout.addLayout(top_row)

        # ========== 分割线 ==========
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        line.setStyleSheet("background-color: #30363d; max-height: 1px;")
        base_layout.addWidget(line)

        # ========== 基础属性列表 ==========
        BASE_KEYS = ["生命白值", "攻击力白值", "防御力白值", "暴击率%", "暴击伤害%"]

        # 存储 spinbox 以便等级切换时更新
        base_spins = {}

        # 创建基础属性的 spinbox
        for key in BASE_KEYS:
            row = QHBoxLayout()
            row.setSpacing(8)
            label = QLabel(key)
            label.setFixedWidth(100)
            row.addWidget(label)

            spin = NoWheelDoubleSpinBox()
            spin.setRange(-999999, 999999)
            spin.setDecimals(2)
            spin.setFixedWidth(120)
            # 初始值：从 sub_stats 中取，若没有则从当前等级的 level_sub_stats 取
            sub_stats = role_data.get("sub_stats", {})
            val = sub_stats.get(key, 0.0)
            if val == 0.0:
                lv = level_combo.currentText()
                lv_data = level_sub_stats.get(lv, {})
                val = lv_data.get(key, 0.0)
            spin.setValue(float(val))
            spin.editingFinished.connect(
                lambda k=key, s=spin: _update_base_stat(role_name, k, s.value())
            )

            row.addWidget(spin)
            row.addStretch()
            base_layout.addLayout(row)
            base_spins[key] = spin

        # 其他 sub_stats（排除基础属性）
        other_sub = {k: v for k, v in role_data.get("sub_stats", {}).items() if k not in BASE_KEYS}
        if other_sub:
            # 添加一个分隔提示
            other_label = QLabel("自定义属性")
            other_label.setStyleSheet("color: #888; font-size: 12px; margin-top: 4px;")
            base_layout.addWidget(other_label)
            add_dict_rows(base_layout, other_sub, ["sub_stats"], window, role_name)

        # ---- 等级切换事件 ----
        def on_level_changed(lv):
            lv_data = level_sub_stats.get(lv, {})
            sub_stats = role_data.setdefault("sub_stats", {})
            for key in BASE_KEYS:
                val = lv_data.get(key, 0.0)
                if key in base_spins:
                    base_spins[key].setValue(float(val))
                sub_stats[key] = val
            role_data["level"] = int(lv) if lv.isdigit() else lv
            _mark_my_role_dirty(window)
            _save_my_roles_silent(window)

        level_combo.currentTextChanged.connect(on_level_changed)

        form.addWidget(group_base)

        # ---- 2. 驱动加成 ----
        # ---- 驱动加成 ----
        group_drive = QGroupBox("驱动加成")
        drive_layout = QVBoxLayout(group_drive)
        drive_layout.setSpacing(8)

        drive_data = role_data.get("drive", {})

        # 2. 显示驱动数量
        drives = drive_data.get("drives", [])
        cnt_label = QLabel(f"已装配驱动数量: {len(drives)}")
        drive_layout.addWidget(cnt_label)

        # 3. 自动计算的 sub_stats 汇总（只读）
        calc_rows = _calc_role_bonus_info(window, role_name)
        if calc_rows:
            info_group = QGroupBox("汇总属性（实时计算）")
            info_group.setStyleSheet(
                "QGroupBox{border:1px solid #30363d;border-radius:5px;padding:8px;}"
            )
            info_layout = QVBoxLayout(info_group)
            for stat, value in calc_rows:
                row = QHBoxLayout()
                row.addWidget(QLabel(stat))
                val_label = QLabel(f"+{value:.2f}")
                val_label.setStyleSheet("color:#58a6ff;font-weight:700;")
                row.addStretch()
                row.addWidget(val_label)
                info_layout.addLayout(row)
            drive_layout.addWidget(info_group)
        else:
            drive_layout.addWidget(QLabel("（暂无驱动/卡带，无法计算汇总属性）"))

        # 4. “查看驱动详情”按钮
        btn_detail = QPushButton("查看驱动详情")
        btn_detail.setObjectName("btnSecondary")
        btn_detail.clicked.connect(
            lambda: _show_drive_details(window, role_name)
        )
        drive_layout.addWidget(btn_detail)

        form.addWidget(group_drive)

        # ---- 3. 弧盘加成 ----
        def build_weapon_group(window, role_name, role_data, form):
            group_weapon = QGroupBox("弧盘加成")
            weapon_layout = QVBoxLayout(group_weapon)
            weapon_layout.setSpacing(8)

            group_weapon = QGroupBox("弧盘加成")
            weapon_layout = QVBoxLayout(group_weapon)
            weapon_layout.setSpacing(8)

            stats = _load_stats(window)
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
                import os, json
                weapon_path = _get_weapon_path(window)
                if not os.path.exists(weapon_path):
                    QMessageBox.warning(window, "错误", f"未找到 weapon.json 文件：{weapon_path}")
                    return
                try:
                    with open(weapon_path, 'r', encoding='utf-8') as f:
                        weapon_db = json.load(f)
                except Exception as e:
                    QMessageBox.warning(window, "错误", f"读取 weapon.json 失败：{e}")
                    return

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

            stats = _load_stats(window)
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
                import os, json
                tapes_path = _get_tape_path(window)
                if not os.path.exists(tapes_path):
                    QMessageBox.warning(window, "错误", f"未找到 tapes.json 文件：{tapes_path}")
                    return
                try:
                    with open(tapes_path, 'r', encoding='utf-8') as f:
                        tapes_db = json.load(f)
                except Exception as e:
                    QMessageBox.warning(window, "错误", f"读取 tapes.json 失败：{e}")
                    return

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
            stats = _load_stats(window)
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
            _save_role_order(window, new_order)

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


def compute_drive_info(drives: list) -> dict:
    """汇总驱动中所有主副属性，返回累加字典"""
    sub_stats = {}
    for d in drives:
        for stats in (d.get("main_stats", {}), d.get("sub_stats", {})):
            for k, v in stats.items():
                sub_stats[k] = sub_stats.get(k, 0.0) + float(v)
    return sub_stats


def _update_field(window, role_name, key, value):
    """更新角色顶层字段."""
    data = window._my_role_form_data
    if data is None:
        return
    data[role_name][key] = value
    _mark_my_role_dirty(window)


def migrate_drive_data(role_data: dict):
    """确保角色的 drive 字段包含 drives,  sub_stats，并计算 sub_stats"""
    drive = role_data.get("drive", {})
    if not isinstance(drive, dict):
        drive = {}
    # 检查是否有 drives 字段
    if "drives" not in drive:
        # sub_stats
        drives = []
        # 但如果没有 drives，则 sub_stats 应为空
        blueprint_layout = drive.get("blueprint_layout", [])
        new_drive = {
            "blueprint_layout": blueprint_layout,
            "drives": drives,
            "sub_stats": compute_drive_info(drives)  # 空
        }
    else:
        # 已有 drives，检查 sub_stats 是否需要更新
        blueprint_layout = drive.get("blueprint_layout", [])
        drives = drive.get("drives", [])
        # 重新计算 sub_stats，覆盖旧的
        new_drive = {
            "blueprint_layout": blueprint_layout,
            "drives": drives,
            "sub_stats": compute_drive_info(drives)
        }
    role_data["drive"] = new_drive
    return role_data


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


def _show_drive_details(window, role_name: str):
    role_data = window._my_role_form_data.get(role_name)

    if not role_data:
        return

    drive_data = role_data.get("drive", {})

    bp = drive_data.get("blueprint_layout", [])
    drives = drive_data.get("drives", [])

    dlg = QDialog(window)
    window._drive_detail_dlg = dlg
    dlg.setWindowTitle(f"{role_name} - 驱动详情")
    dlg.resize(1000, 700)

    root = QVBoxLayout(dlg)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)

    content = QWidget()
    layout = QVBoxLayout(content)

    # 图谱
    if bp:
        group = QGroupBox("拼图图纸")
        group_layout = QVBoxLayout(group)

        row = QHBoxLayout()

        row.addWidget(
            PuzzleBoardWidget(bp),
            0,
            Qt.AlignTop
        )

        # 这里直接复用 Inventory 页
        if hasattr(window, "_bonus_summary_widget"):
            row.addWidget(
                window._bonus_summary_widget(
                    role_name,
                    None,
                    drives
                ),
                0,
                Qt.AlignTop
            )

        row.addStretch()
        group_layout.addLayout(row)
        layout.addWidget(group)

    # 驱动列表
    if drives:
        group = QGroupBox(f"驱动 ({len(drives)}个)")
        group_layout = QVBoxLayout(group)
        weights = role_data.get("weights", {})
        for d in drives:
            quality = d.get("quality", "Gold")
            if hasattr(window, "_score_drive_dict"):
                score = window._score_drive_dict(
                    d.get("sub_stats", {}),
                    d.get("shape_id", ""),
                    weights,
                    quality,
                )
                grade = window._calc_grade(
                    score,
                    window._shape_areas.get(
                        d.get("shape_id", ""),
                        3,
                    ),
                )
            else:
                score = 0
                grade = "-"

            # 创建卡片容器，包含驱动卡片和优化按钮
            drive_container = QWidget()
            drive_container_layout = QVBoxLayout(drive_container)
            drive_container_layout.setContentsMargins(0, 0, 0, 0)
            drive_container_layout.setSpacing(4)

            # 驱动卡片（原有逻辑）
            if hasattr(window, "_equip_card"):
                card = window._equip_card(
                    d.get("shape_id", ""),
                    "",
                    d.get("sub_stats", {}),
                    d.get("shape_id", ""),
                    d.get("uid", ""),
                    weights,
                    (score, grade),
                    quality,
                )
                drive_container_layout.addWidget(card)

            # 优化按钮
            optimize_btn = QPushButton("优化")
            optimize_btn.setObjectName("btnAction")
            optimize_btn.setFixedWidth(60)
            # 用默认参数捕获当前循环变量
            optimize_btn.clicked.connect(
                lambda checked=False, drive=d, rn=role_name, w=weights:
                _show_drive_optimization(window, rn, drive, w)
            )
            drive_container_layout.addWidget(optimize_btn, alignment=Qt.AlignRight)

            group_layout.addWidget(drive_container)

        layout.addWidget(group)
    layout.addStretch()
    scroll.setWidget(content)
    root.addWidget(scroll)
    dlg.exec()
    window._drive_detail_dlg = None


def _calc_role_bonus_info(window, role_name):
    role_data = window._my_role_form_data.get(role_name, {})
    drive = role_data.get("drive", {})
    drives = drive.get("drives", [])

    new_drives = []

    for d in drives:
        d = dict(d)  # 不污染原数据
        shape_id = str(d.get("shape_id", ""))
        # 提取数字
        nums = re.findall(r"\d+", shape_id)
        shape_num = int(nums[0]) if nums else 0
        shape_attack = shape_num * 21
        shape_hp = shape_num * 280
        # 注入到 sub_stats
        sub_stats = dict(d.get("sub_stats", {}))
        sub_stats["攻击力"] = sub_stats.get("攻击力", 0) + shape_attack
        sub_stats["生命值"] = sub_stats.get("生命值", 0) + shape_hp
        d["sub_stats"] = sub_stats
        new_drives.append(d)

    return window._equipment_bonus_rows(role_name, None, new_drives)


def _get_character_total_stats(window, role_name: str) -> dict:
    data = window._my_role_form_data
    if not data or role_name not in data:
        return {}
    role = data[role_name]

    stats = _load_stats(window)
    tape_main_pool = stats.get("tape_main_stat_values", {})  # 可保留，但未使用
    benefit_map = stats.get("benefit_alias_mapping", {})
    alias_map = stats.get("stat_alias_mapping", {})

    total = {}

    def add_stat(key, value):
        if value is None:
            return
        try:
            v = float(value)
        except (ValueError, TypeError):
            return
        canonical = benefit_map.get(key)
        if canonical is None:
            canonical = alias_map.get(key, key)
        total[canonical] = total.get(canonical, 0.0) + v

    # 1. 基础 sub_stats
    for k, v in role.get("sub_stats", {}).items():
        add_stat(k, v)

    # 2. 驱动汇总 sub_stats
    calc_rows = _calc_role_bonus_info(window, role_name)
    for k, v in calc_rows:
        add_stat(k, v)

    # 3. 武器
    weapon = role.get("weapon", {})
    for k, v in weapon.get("sub_stats", {}).items():
        add_stat(k, v)
    w_skill = weapon.get("skill", {})
    for k, v in w_skill.get("sub_stats", {}).items():
        add_stat(k, v)
    w_cover = float(w_skill.get("skill_cover", 0.0))
    w_skill_skill = w_skill.get("skill", {})
    for k, v in w_skill_skill.items():
        add_stat(k, float(v) * w_cover)

    # 4. 空幕（修正缩进，与武器处理平级）
    tape = role.get("tape", {})
    t_cover = float(tape.get("skill_cover", 0.0))

    # 主词条（对象）
    for k, v in tape.get("main_stats", {}).items():
        add_stat(k, float(v))

    # 副属性（sub_stats）
    for k, v in tape.get("sub_stats", {}).items():
        add_stat(k, float(v))

    # 技能1
    for k, v in tape.get("skill", {}).items():
        add_stat(k, float(v))

    # 技能2（受 skill_cover 影响）
    for k, v in tape.get("skill_2", {}).items():
        add_stat(k, float(v) * t_cover)

    return total


def _calc_marginal_benefits(window, total_stats: dict) -> tuple:
    """
    返回: (base_damage, items)
    items 列表每项: (参数名, 当前值字符串, 单位价值字符串, 收益百分比数值)
    已按收益数值从大到小排序。
    """
    stats = _load_stats(window)
    benefit_one = stats.get("benefit_one", {})

    # 单位价值，缺失时默认1.0
    unit_a_base = benefit_one.get("攻击力白值", 1.0) or 1.0
    unit_a_pct = benefit_one.get("攻击力%", 1.25) or 1.25
    unit_a_flat = benefit_one.get("攻击力", 1.0) or 1.0
    unit_elem = benefit_one.get("元素伤害%", 1.25) or 1.25
    unit_dmg = benefit_one.get("伤害增加%", 1.0) or 1.0
    unit_cr = benefit_one.get("暴击率%", 1.0) or 1.0
    unit_cd = benefit_one.get("暴击伤害%", 2.0) or 2.0

    a_base = total_stats.get("攻击力白值", 0.0)
    a_pct = total_stats.get("攻击力%", 0.0)
    a_flat = total_stats.get("攻击力", 0.0)
    elem = total_stats.get("元素伤害%", 0.0)
    dmg = total_stats.get("伤害增加%", 0.0)
    cr_raw = total_stats.get("暴击率%", 0.0)
    cd_raw = total_stats.get("暴击伤害%", 0.0)

    cr = min(cr_raw / 100.0, 1.0)
    cd = cd_raw / 100.0

    def damage(base, pct, flat, elem_val, dmg_val, crit_rate, crit_dmg):
        return (base * (1 + pct / 100.0) + flat) * (1 + (elem_val + dmg_val) / 100.0) * (1 + crit_rate * crit_dmg)

    base_damage = damage(a_base, a_pct, a_flat, elem, dmg, cr, cd)
    if base_damage == 0:
        return 0.0, []  # 返回伤害0和空列表

    items = []

    # 攻击力白值
    step = unit_a_base
    d = damage(a_base + step, a_pct, a_flat, elem, dmg, cr, cd)
    gain = (d / base_damage - 1) * 100
    items.append(("攻击力白值", f"{a_base:.0f}", f"{step:.0f}", gain))

    # 攻击力%
    step = unit_a_pct
    d = damage(a_base, a_pct + step, a_flat, elem, dmg, cr, cd)
    gain = (d / base_damage - 1) * 100
    items.append(("攻击力%", f"{a_pct:.2f}%", f"{step:.2f}%", gain))

    # 攻击力
    step = unit_a_flat
    d = damage(a_base, a_pct, a_flat + step, elem, dmg, cr, cd)
    gain = (d / base_damage - 1) * 100
    items.append(("攻击力", f"{a_flat:.0f}", f"{step:.0f}", gain))

    # 元素伤害%
    step = unit_elem
    d = damage(a_base, a_pct, a_flat, elem + step, dmg, cr, cd)
    gain = (d / base_damage - 1) * 100
    items.append(("元素伤害%", f"{elem:.2f}%", f"{step:.2f}%", gain))

    # 伤害增加%
    step = unit_dmg
    d = damage(a_base, a_pct, a_flat, elem, dmg + step, cr, cd)
    gain = (d / base_damage - 1) * 100
    items.append(("伤害增加%", f"{dmg:.2f}%", f"{step:.2f}%", gain))

    # 暴击率%
    step = unit_cr
    cr_new = min((cr_raw + step) / 100.0, 1.0)
    d = damage(a_base, a_pct, a_flat, elem, dmg, cr_new, cd)
    gain = (d / base_damage - 1) * 100
    items.append(("暴击率%", f"{cr_raw:.2f}%", f"{step:.2f}%", gain))

    # 暴击伤害%
    step = unit_cd
    cd_new = (cd_raw + step) / 100.0
    d = damage(a_base, a_pct, a_flat, elem, dmg, cr, cd_new)
    gain = (d / base_damage - 1) * 100
    items.append(("暴击伤害%", f"{cd_raw:.2f}%", f"{step:.2f}%", gain))

    # 按收益降序排序
    items.sort(key=lambda x: x[3], reverse=True)
    return base_damage, items


def _show_drive_optimization(window, role_name, current_drive, weights):
    """驱动优化替换弹窗"""
    inv_path = runtime.USER_CONFIG_DIR / "real_inventory.json"
    if not inv_path.exists():
        QMessageBox.warning(window, "错误", "real_inventory.json 不存在")
        return

    with open(inv_path, "r", encoding="utf-8") as f:
        try:
            all_drives = json.load(f)
        except Exception:
            QMessageBox.warning(window, "错误", "real_inventory.json 格式错误")
            return

    # 加载 my_roles.json，构建其他角色已装备驱动的 uid -> 角色名列表 映射
    my_roles_data = _load_my_roles(window)
    user_map = {}
    for rn, rdata in my_roles_data.items():
        if rn == role_name:
            continue
        drives = rdata.get("drive", {}).get("drives", [])
        for d in drives:
            uid = d.get("uid")
            if uid:
                if uid not in user_map:
                    user_map[uid] = []
                user_map[uid].append(rn)

    current_shape = current_drive.get("shape_id", "")
    current_uid = current_drive.get("uid", "")

    # 获取当前角色已装备的所有驱动 uid（用于过滤）
    role_data = window._my_role_form_data.get(role_name, {})
    equipped_drives = role_data.get("drive", {}).get("drives", [])
    equipped_uids = {d.get("uid", "") for d in equipped_drives}

    # 计算当前驱动分数
    if hasattr(window, "_score_drive_dict"):
        current_score = window._score_drive_dict(
            current_drive.get("sub_stats", {}),
            current_shape,
            weights,
            current_drive.get("quality", "Gold")
        )
    else:
        current_score = 0

    # 筛选：同形状，非自身，非已装备
    candidates = []
    for d in all_drives:
        if d.get("shape_id") == current_shape and d.get("uid") not in equipped_uids and d.get("uid") != current_uid:
            candidates.append(d)

    if not candidates:
        QMessageBox.information(window, "优化", "没有可替换的驱动")
        return

    # 计算候选分数
    candidate_scores = []
    for d in candidates:
        score = window._score_drive_dict(
            d.get("sub_stats", {}),
            d.get("shape_id", ""),
            weights,
            d.get("quality", "Gold")
        )
        candidate_scores.append((score, d))

    # 按分数降序
    candidate_scores.sort(key=lambda x: x[0], reverse=True)

    # 筛选规则：先取前20个，确保至少3个未被其他角色占用
    final = list(candidate_scores[:20])
    unassigned_count = sum(1 for _, d in final if d.get("uid", "") not in user_map)
    if unassigned_count < 3:
        for s, d in candidate_scores[20:]:
            if d.get("uid", "") not in user_map:
                final.append((s, d))
                unassigned_count += 1
                if unassigned_count >= 3:
                    break

    if not final:
        QMessageBox.information(window, "优化", "没有更好的驱动（或符合条件）")
        return

    # ---------- 替换逻辑 ----------
    def _replace_drive(new_drive):
        """执行驱动替换并保存刷新"""
        # 1. 在当前角色装备中替换
        drives_list = role_data["drive"]["drives"]
        # 找到当前驱动索引
        idx = next((i for i, d in enumerate(drives_list) if d.get("uid") == current_uid), None)
        if idx is not None:
            # 构造新驱动数据（与 my_roles.json 格式一致）
            new_entry = {
                "uid": new_drive["uid"],
                "shape_id": new_drive["shape_id"],
                "sub_stats": new_drive["sub_stats"],
                "quality": new_drive.get("quality", "Gold"),
                "display_name": f"{new_drive['shape_id']}-" + "|".join(
                    f"{k}_{v}" for k, v in new_drive["sub_stats"].items())
            }
            drives_list[idx] = new_entry

            # 2. 如果新驱动被其他角色占用，用空属性驱动替换（不删除，保留占位）
            new_uid = new_drive["uid"]
            if new_uid in user_map:
                for other_role in user_map[new_uid]:
                    other_drives = window._my_role_form_data.get(other_role, {}).get("drive", {}).get("drives", [])
                    for i, od in enumerate(other_drives):
                        if od.get("uid") == new_uid:
                            # 构造一个同形状的空驱动
                            empty_drive = {
                                "uid": f"empty_{new_uid}",
                                "shape_id": od.get("shape_id", ""),
                                "sub_stats": {},
                                "quality": "Gold",
                                "display_name": f"{od.get('shape_id', '')}-(空)"
                            }
                            other_drives[i] = empty_drive
                            break
        # 关闭驱动详情弹窗
        if hasattr(window, '_drive_detail_dlg') and window._drive_detail_dlg:
            window._drive_detail_dlg.accept()
        # 3. 保存并刷新
        _save_my_roles_silent(window)
        dlg.accept()  # 关闭优化弹窗
        _refresh_my_role(window)  # 刷新角色页面（驱动详情弹窗会关闭）

    # ---------- 构建弹窗 ----------
    dlg = QDialog(window)
    dlg.setWindowTitle(f"优化替换 - {current_shape}")
    dlg.resize(800, 600)
    main_layout = QVBoxLayout(dlg)

    # 当前驱动
    cur_group = QGroupBox("当前驱动")
    cur_layout = QVBoxLayout(cur_group)
    if hasattr(window, "_equip_card"):
        cur_card = window._equip_card(
            current_shape,
            "",
            current_drive.get("sub_stats", {}),
            current_shape,
            current_uid,
            weights,
            (current_score,
             window._calc_grade(current_score, window._shape_areas.get(current_shape, 3)) if hasattr(window,
                                                                                                     "_calc_grade") else "-"),
            current_drive.get("quality", "Gold")
        )
        cur_layout.addWidget(cur_card)
    else:
        cur_layout.addWidget(QLabel(f"UID: {current_uid} Score: {current_score:.2f}"))
    main_layout.addWidget(cur_group)

    # 候选驱动
    cand_group = QGroupBox(f"可替换驱动 ({len(final)})")
    cand_layout = QVBoxLayout(cand_group)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll_widget = QWidget()
    scroll_layout = QVBoxLayout(scroll_widget)

    for score, d in final:
        quality = d.get("quality", "Gold")
        uid = d.get("uid", "")
        grade = window._calc_grade(score, window._shape_areas.get(current_shape, 3)) if hasattr(window,
                                                                                                "_calc_grade") else "-"
        if hasattr(window, "_equip_card"):
            card = window._equip_card(
                d.get("shape_id", ""),
                "",
                d.get("sub_stats", {}),
                d.get("shape_id", ""),
                d.get("uid", ""),
                weights,
                (score, grade),
                quality,
            )
            scroll_layout.addWidget(card)
        else:
            scroll_layout.addWidget(QLabel(f"UID: {uid} Score: {score:.2f}"))

        # 如果该驱动被其他角色使用，显示提示
        if uid in user_map:
            user_label = QLabel(f"使用者: {', '.join(user_map[uid])}")
            user_label.setStyleSheet("color: #ff9800; font-size: 12px; margin-left: 10px;")
            scroll_layout.addWidget(user_label)

        # 替换按钮
        replace_btn = QPushButton("替换")
        replace_btn.setObjectName("btnAction")
        # 捕获当前候选驱动
        replace_btn.clicked.connect(lambda checked=False, nd=d: _replace_drive(nd))
        scroll_layout.addWidget(replace_btn)

    scroll_layout.addStretch()
    scroll.setWidget(scroll_widget)
    cand_layout.addWidget(scroll)
    main_layout.addWidget(cand_group)

    # 关闭按钮
    btn_close = QPushButton("关闭")
    btn_close.clicked.connect(dlg.accept)
    main_layout.addWidget(btn_close)

    dlg.exec()
