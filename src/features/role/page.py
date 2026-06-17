# src/features/role/page.py
"""角色详情编辑页面 (my_roles.json)."""

from __future__ import annotations

import json
from pathlib import Path
import re
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
    QInputDialog
)
from PySide6.QtWidgets import QSizePolicy, QHeaderView
from PySide6.QtCore import Qt
from src.ui.puzzle_board import PuzzleBoardWidget
from src.ui.widgets import NoWheelDoubleSpinBox, SearchableComboBox, match_pinyin
from src.app import runtime

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


def _get_stats_path(window) -> Path:
    config_dir = _get_config_dir(window)
    return config_dir / "stats.json"


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
    """保存当前编辑的数据到 my_roles.json."""
    data = getattr(window, "_my_role_form_data", None)
    if data is None:
        QMessageBox.information(window, "提示", "没有需要保存的数据。")
        return
    filepath = _get_my_roles_path()
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    window._my_role_dirty = False
    QMessageBox.information(window, "保存", "my_roles.json 已保存")


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

    all_names = sorted(data.keys())
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
        margins = _calc_marginal_benefits(window, total_stats)
        if margins:
            # 根据权重过滤边际收益词条
            stats_config = _load_stats(window)
            alias_map = stats_config.get("benefit_alias_mapping", {})
            weights = role_data.get("weights", {})
            allowed_categories = set()
            for weight_key in weights.keys():
                canonical = alias_map.get(weight_key, weight_key)
                allowed_categories.add(canonical)
            # 只保留允许的类别
            margins = [m for m in margins if m[0] in allowed_categories]
        if margins:
            group_margin = QGroupBox("边际收益（按每单位收益排序）")
            margin_layout = QVBoxLayout(group_margin)

            table = QTableWidget()
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(["参数", "当前值", "1单位", "每单位提升"])
            table.setRowCount(len(margins))
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.verticalHeader().setVisible(False)

            # 去掉滚动条
            table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

            for i, (name, cur_val, unit_val, gain) in enumerate(margins):
                table.setItem(i, 0, QTableWidgetItem(name))
                table.setItem(i, 1, QTableWidgetItem(cur_val))
                table.setItem(i, 2, QTableWidgetItem(unit_val))
                gain_item = QTableWidgetItem(f"{gain:.4f}%")
                table.setItem(i, 3, gain_item)

            # 四列均匀拉伸填满宽度
            header = table.horizontalHeader()
            for col in range(4):
                header.setSectionResizeMode(col, QHeaderView.Stretch)

            # 固定高度 = 表头 + 数据行 + 边框
            header_height = header.height()
            row_height = table.verticalHeader().defaultSectionSize()
            frame = table.frameWidth() * 2
            total_height = header_height + row_height * len(margins) + frame
            table.setFixedHeight(total_height)

            margin_layout.addWidget(table)
            form.addWidget(group_margin)

        # ---- 1. 基础加成 ----
        group_base = QGroupBox("基础加成")
        group_base.setStyleSheet("QGroupBox{font-weight:bold;}")
        base_layout = QVBoxLayout(group_base)
        base_layout.setSpacing(8)

        # 等级
        level_val = role_data.get("level", 70)
        add_single_value_row(
            base_layout, "等级:", ["level"], window, role_name,
            default=level_val, is_float=False
        )
        # info 字典
        info_data = role_data.get("info", {})
        add_dict_rows(base_layout, info_data, ["info"], window, role_name)
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

        # 3. 自动计算的 info 汇总（只读）
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
        group_weapon = QGroupBox("弧盘加成")
        weapon_layout = QVBoxLayout(group_weapon)
        weapon_layout.setSpacing(8)

        stats = _load_stats(window)
        tape_pool = stats.get("tape_stat_values", {})

        weapon_data = role_data.get("weapon")
        if not isinstance(weapon_data, dict):
            weapon_data = {}
            role_data["weapon"] = weapon_data

        weapon_data.setdefault("name", "")
        weapon_data.setdefault("skill_cover", 0.8)
        weapon_data.setdefault("skill", {})
        weapon_data.setdefault("info", {})

        wskill = weapon_data["skill"]
        winfo = weapon_data["info"]

        # =========================
        # 名称
        # =========================
        add_single_value_row(
            weapon_layout,
            "名称:",
            ["weapon", "name"],
            window,
            role_name,
            default=weapon_data["name"],
            is_str=True
        )

        # =========================
        # skill_cover
        # =========================
        add_single_value_row(
            weapon_layout,
            "技能覆盖:",
            ["weapon", "skill_cover"],
            window,
            role_name,
            default=float(weapon_data["skill_cover"]),
            is_float=True
        )

        # =========================
        # 技能数据（单key + 下拉，允许清空）
        # =========================
        skill_label = QLabel("技能数据：")
        skill_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
        weapon_layout.addWidget(skill_label)

        if not tape_pool:
            tape_pool = {"攻击力%": 0}

        # 保证 wskill 为单键字典或空
        if not isinstance(wskill, dict):
            wskill = {}
        if len(wskill) > 1:
            k = next(iter(wskill))
            v = wskill[k]
            wskill.clear()
            wskill[k] = v

        # 当前技能键和值，若为空则留空
        skill_key = next(iter(wskill.keys())) if wskill else ""
        skill_val = float(wskill.get(skill_key, 0.0)) if skill_key else 0.0

        skill_row = QHBoxLayout()

        skill_combo = SearchableComboBox()
        skill_combo.addItem("")  # 空选项，表示不设置技能
        skill_combo.addItems(list(tape_pool.keys()))
        if skill_key in tape_pool:
            skill_combo.setCurrentText(skill_key)
        else:
            skill_combo.setCurrentIndex(0)

        skill_spin = NoWheelDoubleSpinBox()
        skill_spin.setRange(-999999, 999999)
        skill_spin.setDecimals(2)
        skill_spin.setValue(skill_val)

        def commit_skill():
            k = skill_combo.currentText().strip()
            v = skill_spin.value()
            if k and k in tape_pool:
                weapon_data["skill"] = {k: v}
            else:
                weapon_data["skill"] = {}  # 清空技能
            _save_my_roles_silent(window)

        skill_combo.currentTextChanged.connect(lambda _: commit_skill())
        skill_spin.editingFinished.connect(commit_skill)

        skill_row.addWidget(skill_combo)
        skill_row.addWidget(skill_spin)
        weapon_layout.addLayout(skill_row)

        # =========================
        # 额外加成（info，允许第二个属性置空）
        # =========================
        info_label = QLabel("额外加成：")
        info_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
        weapon_layout.addWidget(info_label)

        winfo.setdefault("攻击力白值", 300.0)

        def safe_float(v):
            try:
                if v is None or v == "":
                    return 0.0
                return float(v)
            except:
                return 0.0

        stats_keys = list(tape_pool.keys()) if tape_pool else ["暴击率%"]

        # 提取第二个键（如果没有就留空）
        second_key = None
        for k in winfo:
            if k != "攻击力白值":
                second_key = k
                break
        # 不再自动补全
        white_spin = NoWheelDoubleSpinBox()
        white_spin.setRange(-999999, 999999)
        white_spin.setValue(float(winfo.get("攻击力白值", 300.0)))

        second_combo = SearchableComboBox()
        second_combo.addItem("")  # 空选项
        second_combo.addItems(stats_keys)
        if second_key and second_key in stats_keys:
            second_combo.setCurrentText(second_key)
        else:
            second_combo.setCurrentIndex(0)  # 选中空

        second_spin = NoWheelDoubleSpinBox()
        second_spin.setRange(-999999, 999999)
        second_spin.setValue(safe_float(winfo.get(second_key, 0.0)) if second_key else 0.0)

        def commit_info():
            key = second_combo.currentText().strip()
            white_val = white_spin.value()
            new_info = {"攻击力白值": white_val}
            if key and key in tape_pool:
                new_info[key] = second_spin.value()
            weapon_data["info"] = new_info
            _save_my_roles_silent(window)

        white_spin.editingFinished.connect(commit_info)
        second_combo.currentTextChanged.connect(lambda _: commit_info())
        second_spin.editingFinished.connect(commit_info)

        info_row1 = QHBoxLayout()
        info_row1.addWidget(QLabel("攻击力白值"))
        info_row1.addWidget(white_spin)

        info_row2 = QHBoxLayout()
        info_row2.addWidget(second_combo)
        info_row2.addWidget(second_spin)

        weapon_layout.addLayout(info_row1)
        weapon_layout.addLayout(info_row2)

        form.addWidget(group_weapon)

        # ---- 4. 空幕加成 ----
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

        tape_data.setdefault("main", "")
        tape_data.setdefault("skill_cover", 0.8)
        tape_data.setdefault("skill", {})
        tape_data.setdefault("skill_2", {})
        tape_data.setdefault("info", {})

        # ====== 类型安全保护 ======
        if not isinstance(tape_data["main"], str):
            tape_data["main"] = ""
        for k in ("skill", "skill_2", "info"):
            if not isinstance(tape_data[k], dict):
                tape_data[k] = {}

        tape_skill = tape_data["skill"]
        tape_skill2 = tape_data["skill_2"]
        tape_info = tape_data["info"]

        # =========================
        # 主词条（允许留空）
        # =========================
        main_label = QLabel("主词条：")
        main_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
        tape_layout.addWidget(main_label)

        main_keys = list(tape_main_pool.keys()) if tape_main_pool else ["攻击力%"]
        main_items = [f"{k} ({v})" for k, v in tape_main_pool.items()]

        main_row = QHBoxLayout()
        main_combo = SearchableComboBox()
        main_combo.addItem("")  # 空选项，表示不设置主词条
        main_combo.addItems(main_items)

        # 反显已保存的主词条
        saved_main_key = tape_data["main"]
        if saved_main_key and saved_main_key in tape_main_pool:
            main_combo.setCurrentText(f"{saved_main_key} ({tape_main_pool[saved_main_key]})")
        else:
            main_combo.setCurrentIndex(0)  # 默认选择空

        # 右侧数值标签（空选择时留空）
        main_value_label = QLabel()
        main_value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        main_value_label.setStyleSheet("background-color:#2a2a2a; padding:2px 6px; border-radius:3px;")

        def update_main_value():
            text = main_combo.currentText()
            if text:
                key = text.rsplit(" (", 1)[0]
                val = tape_main_pool.get(key, 0)
                main_value_label.setText(str(val))
            else:
                main_value_label.setText("")

        update_main_value()

        def commit_main():
            text = main_combo.currentText()
            if text:
                key = text.rsplit(" (", 1)[0]
                tape_data["main"] = key
            else:
                tape_data["main"] = ""  # 清空
            update_main_value()
            _save_my_roles_silent(window)  # 立即静默保存

        main_combo.currentTextChanged.connect(lambda _: commit_main())

        main_row.addWidget(main_combo)
        main_row.addWidget(main_value_label)
        tape_layout.addLayout(main_row)

        # =========================
        # skill_cover
        # =========================
        add_single_value_row(
            tape_layout,
            "技能覆盖:",
            ["tape", "skill_cover"],
            window,
            role_name,
            default=float(tape_data["skill_cover"]),
            is_float=True
        )

        # =========================
        # 技能数据 skill 和 skill_2（单key，下拉来自 tape_stat_values）
        # =========================
        if not tape_pool:
            tape_pool = {"攻击力%": 0}

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
                _mark_my_role_dirty(window)

            combo.currentTextChanged.connect(lambda _: commit())
            spin.editingFinished.connect(commit)

            row.addWidget(combo)
            row.addWidget(spin)
            tape_layout.addLayout(row)

        create_single_skill_row(tape_skill, "技能1：")
        create_single_skill_row(tape_skill2, "技能2：")

        # 额外属性（最多4条，允许留空）
        info_label = QLabel("额外属性（最多4条，留空表示未设置）：")
        info_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
        tape_layout.addWidget(info_label)

        # 准备4个槽位，已存在的词条优先，不足用空位填充
        info_entries = list(tape_info.items())
        while len(info_entries) < 4:
            info_entries.append(("", 0.0))
        info_entries = info_entries[:4]

        tape_pool_items = [f"{k} ({v})" for k, v in tape_pool.items()]
        info_widgets = []

        for idx, (key, val) in enumerate(info_entries):
            row = QHBoxLayout()
            combo = SearchableComboBox()
            combo.addItem("")  # 空选项，代表未设置
            combo.addItems(tape_pool_items)
            if key and key in tape_pool:
                combo.setCurrentText(f"{key} ({tape_pool[key]})")
            else:
                combo.setCurrentIndex(0)  # 选择空

            value_label = QLabel()
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value_label.setStyleSheet("background-color:#2a2a2a; padding:2px 6px; border-radius:3px;")

            def update_value_label(cb, lbl):
                text = cb.currentText()
                if text:
                    k = text.rsplit(" (", 1)[0]
                    v = tape_pool.get(k, 0)
                    lbl.setText(str(v))
                else:
                    lbl.setText("")

            update_value_label(combo, value_label)
            combo.currentTextChanged.connect(lambda _, c=combo, l=value_label: update_value_label(c, l))

            row.addWidget(combo)
            row.addWidget(value_label)
            tape_layout.addLayout(row)
            info_widgets.append((combo, value_label))

        def commit_info():
            new_info = {}
            for combo, lbl in info_widgets:
                text = combo.currentText().strip()
                if text:  # 空项不加入
                    key = text.rsplit(" (", 1)[0]
                    val = tape_pool.get(key, 0)
                    new_info[key] = val
            tape_data["info"] = new_info
            _save_my_roles_silent(window)  # 自动保存

        for combo, lbl in info_widgets:
            combo.currentTextChanged.connect(lambda _, c=combo, l=lbl: commit_info())

        form.addWidget(group_tape)

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
                _save_my_roles(window)  # 需要定义静默保存
                _refresh_my_role(window)  # 全量刷新

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
                _save_my_roles(window)
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
        all_names = sorted(data.keys())
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
    info = {}
    for d in drives:
        for stats in (d.get("main_stats", {}), d.get("sub_stats", {})):
            for k, v in stats.items():
                info[k] = info.get(k, 0.0) + float(v)
    return info


def _update_field(window, role_name, key, value):
    """更新角色顶层字段."""
    data = window._my_role_form_data
    if data is None:
        return
    data[role_name][key] = value
    _mark_my_role_dirty(window)


def migrate_drive_data(role_data: dict):
    """确保角色的 drive 字段包含 drives,  info，并计算 info"""
    drive = role_data.get("drive", {})
    if not isinstance(drive, dict):
        drive = {}
    # 检查是否有 drives 字段
    if "drives" not in drive:
        # info
        drives = []
        # 但如果没有 drives，则 info 应为空
        blueprint_layout = drive.get("blueprint_layout", [])
        new_drive = {
            "blueprint_layout": blueprint_layout,
            "drives": drives,
            "info": compute_drive_info(drives)  # 空
        }
    else:
        # 已有 drives，检查 info 是否需要更新
        blueprint_layout = drive.get("blueprint_layout", [])
        drives = drive.get("drives", [])
        # 重新计算 info，覆盖旧的
        new_drive = {
            "blueprint_layout": blueprint_layout,
            "drives": drives,
            "info": compute_drive_info(drives)
        }
    role_data["drive"] = new_drive
    return role_data


def _update_info_field(window, role_name, key, value):
    """更新 info 子字段."""
    data = window._my_role_form_data
    if data is None:
        return
    data[role_name].setdefault("info", {})[key] = value
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

            # 直接复用 inventory 的驱动卡片
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
                group_layout.addWidget(card)
        layout.addWidget(group)
    layout.addStretch()
    scroll.setWidget(content)
    root.addWidget(scroll)
    dlg.exec()


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
        # 注入到 sub_stats（最安全，因为你的汇总就是扫 sub_stats）
        sub = dict(d.get("sub_stats", {}))
        sub["攻击力"] = sub.get("攻击力", 0) + shape_attack
        sub["生命值"] = sub.get("生命值", 0) + shape_hp
        d["sub_stats"] = sub
        new_drives.append(d)

    return window._equipment_bonus_rows(role_name, None, new_drives)


def _get_character_total_stats(window, role_name: str) -> dict:
    """计算角色所有属性汇总（覆盖率已乘，别名已统一），返回字典 {规范名称: 值}"""
    data = window._my_role_form_data
    if not data or role_name not in data:
        return {}
    role = data[role_name]

    stats = _load_stats(window)
    tape_main_pool = stats.get("tape_main_stat_values", {})
    # 优先使用 benefit_alias_mapping，未命中再用 stat_alias_mapping，最后保留原键
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
        # 先看 benefit 映射，再看 stat 映射，都没命中则用原键
        canonical = benefit_map.get(key)
        if canonical is None:
            canonical = alias_map.get(key, key)
        total[canonical] = total.get(canonical, 0.0) + v

    # 1. 基础 info
    for k, v in role.get("info", {}).items():
        add_stat(k, v)

    # 2. 驱动汇总 info
    calc_rows = _calc_role_bonus_info(window, role_name)
    for k, v in calc_rows:
        add_stat(k, v)

    # 3. 武器
    weapon = role.get("weapon", {})
    for k, v in weapon.get("info", {}).items():
        add_stat(k, v)
    w_skill = weapon.get("skill", {})
    w_cover = float(weapon.get("skill_cover", 0.0))
    for k, v in w_skill.items():
        add_stat(k, float(v) * w_cover)

    # 4. 空幕
    tape = role.get("tape", {})
    t_cover = float(tape.get("skill_cover", 0.0))
    main_key = tape.get("main", "")
    if main_key and main_key in tape_main_pool:
        add_stat(main_key, tape_main_pool[main_key])
    for k, v in tape.get("skill", {}).items():
        add_stat(k, float(v))
    for k, v in tape.get("skill_2", {}).items():
        add_stat(k, float(v) * t_cover)
    for k, v in tape.get("info", {}).items():
        add_stat(k, v)

    return total


def _calc_marginal_benefits(window, total_stats: dict) -> list:
    """
    返回列表，每项: (参数名, 当前值字符串, 单位价值字符串, 收益百分比数值)
    已按收益数值从大到小排序。
    """
    stats = _load_stats(window)
    benefit_one = stats.get("benefit_one", {})

    # 单位价值，缺失时默认1.0
    unit_a_base = benefit_one.get("攻击力白值", 1.0) or 1.0
    unit_a_pct = benefit_one.get("攻击力%", 1.0) or 1.0
    unit_a_flat = benefit_one.get("攻击力", 1.0) or 1.0
    unit_elem = benefit_one.get("元素伤害%", 1.0) or 1.0
    unit_dmg = benefit_one.get("伤害增加%", 1.0) or 1.0
    unit_cr = benefit_one.get("暴击率%", 1.0) or 1.0
    unit_cd = benefit_one.get("暴击伤害%", 1.0) or 1.0

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
        return []

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
    return items
