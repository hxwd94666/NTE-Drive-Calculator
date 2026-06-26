"""弧盘（武器）相关 UI 组件"""

import time
import random
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGroupBox,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
    QInputDialog,
    QMessageBox,
    QComboBox,
)

from src.ui.widgets import NoWheelDoubleSpinBox, SearchableComboBox
from .dao import load_stats, load_weapons, save_my_roles
from .core import get_character_total_stats, calc_base_damage


def clear_layout(layout):
    """递归清除布局中的所有子项，但不删除 layout 本身"""
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())
            item.layout().deleteLater()


def build_weapon_group(
        parent_layout,
        window,
        role_name: str,
        role_data: dict,
        on_save_callback,
        on_margin_refresh_callback=None,
):
    """
    构建弧盘加成 QGroupBox 并添加到 parent_layout
    """
    group_weapon = QGroupBox("弧盘加成")
    weapon_layout = QVBoxLayout(group_weapon)
    weapon_layout.setSpacing(8)

    # 存储必要信息以便刷新
    group_weapon._window = window
    group_weapon._role_name = role_name
    group_weapon._role_data = role_data
    group_weapon._on_save_callback = on_save_callback
    group_weapon._on_margin_refresh_callback = on_margin_refresh_callback
    group_weapon._current_weapon_info = None  # 存储当前武器模板信息

    # 构建内容
    _build_weapon_group_content(group_weapon)

    parent_layout.addWidget(group_weapon)
    return group_weapon


def _build_weapon_group_content(group_weapon):
    """构建弧盘组的内容（可被刷新复用）"""
    clear_layout(group_weapon.layout())
    layout = group_weapon.layout()

    window = group_weapon._window
    role_data = group_weapon._role_data
    on_save_callback = group_weapon._on_save_callback
    on_margin_refresh_callback = group_weapon._on_margin_refresh_callback

    stats = load_stats()
    tape_pool = stats.get("tape_stat_values", {})
    skill_pool = stats.get("skill_pool", [])  # 技能增幅类型池

    weapon_data = role_data.get("weapon")
    if not isinstance(weapon_data, dict):
        weapon_data = {}
        role_data["weapon"] = weapon_data

    weapon_data.setdefault("name", "")
    weapon_data.setdefault("sub_stats", {})
    weapon_data.setdefault("skill", [])
    # 等级和混频默认值
    weapon_data.setdefault("level", 80)
    weapon_data.setdefault("mix_level", 1)

    # 如果当前有武器名称，但尚未加载武器信息，则自动加载
    weapon_name = weapon_data.get("name")
    if weapon_name and group_weapon._current_weapon_info is None:
        weapon_db = load_weapons()
        group_weapon._current_weapon_info = weapon_db.get(weapon_name)

    def safe_float(v):
        try:
            return float(v) if v not in (None, "") else 0.0
        except:
            return 0.0

    # ---- 定义更新边际收益标签的函数 ----
    def _update_margin_label_ui():
        try:
            role_data = group_weapon._role_data
            no_weapon_data = {k: v for k, v in role_data.items() if k != "weapon"}
            stats_without = get_character_total_stats(no_weapon_data)
            damage_without = calc_base_damage(stats_without)

            stats_with = get_character_total_stats(role_data)
            damage_with = calc_base_damage(stats_with)

            if damage_without == 0:
                gain = 0.0
            else:
                gain = (damage_with / damage_without - 1) * 100
            margin_label.setText(f"直伤收益: {gain:+.2f}%")
        except Exception as e:
            margin_label.setText("直伤收益: 计算错误")
            print(f"计算弧盘边际收益失败: {e}")

    group_weapon._update_margin_label_ui = _update_margin_label_ui

    # 统一的数据变更处理函数
    def _on_data_changed():
        on_save_callback()
        if hasattr(group_weapon, '_update_margin_label_ui'):
            group_weapon._update_margin_label_ui()
        if on_margin_refresh_callback:
            on_margin_refresh_callback()

    # =========================
    # 1. 名称行（带选取按钮 + 等级下拉 + 混频等级下拉）
    # =========================
    name_row = QHBoxLayout()
    name_row.addWidget(QLabel("名称:"))

    name_edit = QLineEdit()
    name_edit.setText(weapon_data.get("name", ""))
    name_edit.textChanged.connect(_on_data_changed)
    name_row.addWidget(name_edit)

    # 弹性空间
    name_row.addStretch()

    # 武器等级下拉（从当前武器模板的 level_sub_stats 获取可用等级）
    weapon_info = group_weapon._current_weapon_info
    available_levels = ["1", "20", "30", "40", "50", "60", "70", "80"]
    if weapon_info and "level_sub_stats" in weapon_info:
        levels = sorted(weapon_info["level_sub_stats"].keys(), key=lambda x: int(x))
        if levels:
            available_levels = levels

    level_combo = QComboBox()
    level_combo.addItems(available_levels)
    current_level = str(weapon_data.get("level", 80))
    if current_level in available_levels:
        level_combo.setCurrentText(current_level)
    else:
        level_combo.setCurrentIndex(0)
    level_combo.setFixedWidth(60)
    level_combo.setStyleSheet("font-size:13px; padding:4px;")
    name_row.addWidget(QLabel("等级:"))
    name_row.addWidget(level_combo)

    # 混频等级下拉
    mix_combo = QComboBox()
    mix_combo.addItems([str(i) for i in range(1, 6)])
    current_mix = str(weapon_data.get("mix_level", 1))
    mix_combo.setCurrentText(current_mix)
    mix_combo.setFixedWidth(60)
    mix_combo.setStyleSheet("font-size:13px; padding:4px;")
    name_row.addWidget(QLabel("混频:"))
    name_row.addWidget(mix_combo)

    # 边际收益标签
    margin_label = QLabel("直伤收益: 0.00%")
    margin_label.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 13px;")
    name_row.addWidget(margin_label)

    def _load_weapon_data():
        weapon_db = load_weapons()
        weapon_type = role_data.get("weapon_type", "")
        names = list(weapon_db.keys())
        if weapon_type:
            names = [name for name in names if weapon_db[name].get("type") == weapon_type]
        if not names:
            QMessageBox.information(window, "提示", f"没有找到与武器类型 '{weapon_type}' 匹配的弧盘")
            return

        selected, ok = QInputDialog.getItem(window, "选择弧盘", "请选择弧盘：", names, 0, False)
        if not ok or not selected:
            return

        weapon_info = weapon_db[selected]

        # 保存当前武器模板
        group_weapon._current_weapon_info = weapon_info

        # 确定默认等级：优先取武器模板中的 level 字段，否则 80
        default_level = weapon_info.get("level", 80)
        weapon_data["level"] = default_level

        # 确定默认混频等级：优先取武器模板中的 mix_level 字段，否则取第一个可用等级
        default_mix = weapon_info.get("mix_level", 1)
        mix_levels = weapon_info.get("mix_level_sub_stats", {})
        if mix_levels:
            available_mix_levels = sorted(mix_levels.keys(), key=lambda x: int(x))
            if str(default_mix) not in available_mix_levels:
                default_mix = int(available_mix_levels[0]) if available_mix_levels else 1
        weapon_data["mix_level"] = default_mix

        # 更新名称
        weapon_data["name"] = selected

        # 从 level_sub_stats 中获取对应等级的 sub_stats
        level_sub_stats = weapon_info.get("level_sub_stats", {})
        level_key = str(default_level)
        if level_key in level_sub_stats:
            weapon_data["sub_stats"] = level_sub_stats[level_key].copy()
        else:
            # 如果该等级不存在，取第一个可用等级
            first_level = sorted(level_sub_stats.keys(), key=lambda x: int(x))[0] if level_sub_stats else "80"
            weapon_data["sub_stats"] = level_sub_stats.get(first_level, {}).copy()

        # 从 mix_level_sub_stats 中获取对应等级的 skill
        selected_mix = mix_levels.get(str(default_mix), {})
        weapon_data["skill"] = selected_mix.get("skill", []).copy()

        # 刷新整个组
        _refresh_weapon_group(group_weapon)
        # 刷新边际收益
        if on_margin_refresh_callback:
            on_margin_refresh_callback()

    def _on_level_changed(level_str):
        """等级改变时更新 sub_stats"""
        try:
            new_level = int(level_str)
        except ValueError:
            return
        weapon_data["level"] = new_level

        weapon_info = group_weapon._current_weapon_info
        if not weapon_info:
            return
        level_sub_stats = weapon_info.get("level_sub_stats", {})
        level_key = str(new_level)
        if level_key in level_sub_stats:
            weapon_data["sub_stats"] = level_sub_stats[level_key].copy()
        else:
            # 如果该等级不存在，尝试取最近的等级
            available = sorted(level_sub_stats.keys(), key=lambda x: int(x))
            if available:
                closest = min(available, key=lambda x: abs(int(x) - new_level))
                weapon_data["sub_stats"] = level_sub_stats[closest].copy()
            else:
                weapon_data["sub_stats"] = {}
        # 刷新组
        _refresh_weapon_group(group_weapon)
        _on_data_changed()

    def _on_mix_level_changed(mix_str):
        """混频等级改变时更新 skill"""
        try:
            new_mix = int(mix_str)
        except ValueError:
            return
        weapon_data["mix_level"] = new_mix

        weapon_info = group_weapon._current_weapon_info
        if not weapon_info:
            return
        mix_levels = weapon_info.get("mix_level_sub_stats", {})
        mix_key = str(new_mix)
        selected_mix = mix_levels.get(mix_key, {})
        weapon_data["skill"] = selected_mix.get("skill", []).copy()
        # 刷新组
        _refresh_weapon_group(group_weapon)
        _on_data_changed()

    select_btn = QPushButton("选取弧盘")
    select_btn.setObjectName("btnAction")
    select_btn.clicked.connect(_load_weapon_data)
    name_row.addWidget(select_btn)

    layout.addLayout(name_row)

    # 连接信号
    level_combo.currentTextChanged.connect(_on_level_changed)
    mix_combo.currentTextChanged.connect(_on_mix_level_changed)

    # =========================
    # 2. 基础加成
    # =========================
    base_label = QLabel("基础加成：")
    base_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
    layout.addWidget(base_label)

    base_info = weapon_data["sub_stats"]
    base_info.setdefault("攻击力白值", 300.0)

    existing_keys = [k for k in base_info.keys() if k != "攻击力白值"]
    second_key = existing_keys[0] if len(existing_keys) >= 1 else None

    white_spin = NoWheelDoubleSpinBox()
    white_spin.setRange(-999999, 999999)
    white_spin.setValue(float(base_info.get("攻击力白值", 300.0)))
    row1 = QHBoxLayout()
    row1.addWidget(QLabel("攻击力白值"))
    row1.addWidget(white_spin)
    layout.addLayout(row1)

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
    layout.addLayout(row2)

    def commit_base():
        new_base = {"攻击力白值": white_spin.value()}
        k2 = combo2.currentText().strip()
        if k2 and k2 in tape_pool:
            new_base[k2] = spin2.value()
        weapon_data["sub_stats"] = new_base
        _on_data_changed()

    white_spin.editingFinished.connect(commit_base)
    combo2.currentTextChanged.connect(lambda _: commit_base())
    spin2.editingFinished.connect(commit_base)

    # =========================
    # 3. 技能加成
    # =========================
    skill_label = QLabel("技能加成：")
    skill_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
    layout.addWidget(skill_label)

    skill_effects = weapon_data.get("skill", [])
    if not isinstance(skill_effects, list):
        skill_effects = []
        weapon_data["skill"] = skill_effects

    ss_container = QWidget()
    ss_layout = QVBoxLayout(ss_container)
    ss_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(ss_container)

    ss_rows = []

    def add_ss_row(effect=None):
        if effect is None:
            effect = {"key": "", "value": 0.0, "cover": 0.8}

        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)

        key_combo = SearchableComboBox()
        key_combo.addItems(skill_pool)
        key_combo.setEditable(True)
        key_combo.setCurrentText(effect.get("key", ""))
        key_combo.setMinimumWidth(250)
        key_combo.setPlaceholderText("选择或输入属性名")
        row_layout.addWidget(QLabel("属性"))
        row_layout.addWidget(key_combo)

        value_spin = NoWheelDoubleSpinBox()
        value_spin.setRange(-999999, 999999)
        value_spin.setDecimals(2)
        value_spin.setValue(safe_float(effect.get("value", 0.0)))
        row_layout.addWidget(QLabel("数值"))
        row_layout.addWidget(value_spin)

        cover_spin = NoWheelDoubleSpinBox()
        cover_spin.setRange(0, 1.0)
        cover_spin.setSingleStep(0.05)
        cover_spin.setDecimals(2)
        cover_spin.setValue(float(effect.get("cover", 0.8)))
        row_layout.addWidget(QLabel("覆盖率"))
        row_layout.addWidget(cover_spin)

        num_spin = NoWheelDoubleSpinBox()
        num_spin.setRange(0, 999)
        num_spin.setDecimals(0)
        num_spin.setValue(int(effect.get("num", 0)) if effect.get("num") else 0)
        num_spin.setFixedWidth(60)
        row_layout.addWidget(QLabel("层数"))
        row_layout.addWidget(num_spin)

        del_btn = QPushButton("✕")
        del_btn.setFixedSize(28, 28)
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
        del_btn.setFont(QFont("Arial", 14))
        row_layout.addWidget(del_btn)

        ss_layout.addWidget(row_widget)
        row_data = {
            "key_combo": key_combo,
            "value_spin": value_spin,
            "cover_spin": cover_spin,
            "num_spin": num_spin,
            "row_widget": row_widget
        }
        ss_rows.append(row_data)

        def remove_row():
            if row_data in ss_rows:
                ss_layout.removeWidget(row_widget)
                row_widget.deleteLater()
                ss_rows.remove(row_data)
                commit_ss_all()

        del_btn.clicked.connect(remove_row)

        def commit_ss_one():
            commit_ss_all()

        key_combo.currentTextChanged.connect(lambda _: commit_ss_one())
        value_spin.editingFinished.connect(commit_ss_one)
        cover_spin.editingFinished.connect(commit_ss_one)
        num_spin.editingFinished.connect(commit_ss_one)

        return row_widget

    def commit_ss_all():
        new_list = []
        for row_data in ss_rows:
            key = row_data["key_combo"].currentText().strip()
            if not key:
                continue
            effect = {
                "key": key,
                "value": row_data["value_spin"].value(),
                "cover": row_data["cover_spin"].value(),
            }
            num = int(row_data["num_spin"].value())
            if num > 0:
                effect["num"] = num
            new_list.append(effect)
        weapon_data["skill"] = new_list
        _on_data_changed()

    for effect in skill_effects:
        add_ss_row(effect)

    ss_add_btn = QPushButton("+ 添加技能效果")
    ss_add_btn.clicked.connect(lambda: add_ss_row())
    layout.addWidget(ss_add_btn)

    # 初始更新边际收益标签
    _update_margin_label_ui()


def _refresh_weapon_group(group_weapon):
    """刷新弧盘组内容（内部使用）"""
    window = group_weapon._window
    role_name = group_weapon._role_name
    role_data = window._my_role_form_data.get(role_name, {})
    group_weapon._role_data = role_data
    _build_weapon_group_content(group_weapon)


def refresh_weapon_group(window, role_name: str):
    """外部刷新弧盘组"""
    if not hasattr(window, "_weapon_groups"):
        return
    group_weapon = window._weapon_groups.get(role_name)
    if group_weapon:
        _refresh_weapon_group(group_weapon)
