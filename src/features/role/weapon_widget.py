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
            # 递归清除子布局的内容
            clear_layout(item.layout())
            # 子布局已空，可以删除
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

    Args:
        parent_layout: 父布局
        window: 主窗口对象
        role_name: 角色名
        role_data: 角色数据
        on_save_callback: 保存回调函数
        on_margin_refresh_callback: 边际收益刷新回调函数（可选）

    Returns:
        QGroupBox: 创建的弧盘组
    """
    group_weapon = QGroupBox("弧盘加成")
    weapon_layout = QVBoxLayout(group_weapon)
    weapon_layout.setSpacing(8)

    # 存储必要信息以便刷新
    group_weapon._window = window
    group_weapon._role_name = role_name
    group_weapon._role_data = role_data
    group_weapon._on_save_callback = on_save_callback
    group_weapon._on_margin_refresh_callback = on_margin_refresh_callback  # ✅

    # 构建内容
    _build_weapon_group_content(group_weapon)

    parent_layout.addWidget(group_weapon)
    return group_weapon


def _build_weapon_group_content(group_weapon):
    """构建弧盘组的内容（可被刷新复用）"""
    # 清除现有内容
    clear_layout(group_weapon.layout())

    # 重新获取布局引用（主布局仍然存在，只是被清空了）
    layout = group_weapon.layout()

    window = group_weapon._window
    role_data = group_weapon._role_data
    on_save_callback = group_weapon._on_save_callback
    on_margin_refresh_callback = group_weapon._on_margin_refresh_callback

    stats = load_stats()
    tape_pool = stats.get("tape_stat_values", {})
    tape_main_pool_value = stats.get("tape_main_stat_values", {})

    weapon_data = role_data.get("weapon")
    if not isinstance(weapon_data, dict):
        weapon_data = {}
        role_data["weapon"] = weapon_data

    weapon_data.setdefault("name", "")
    weapon_data.setdefault("sub_stats", {})
    weapon_data.setdefault("skill", {})
    skill_obj = weapon_data["skill"]
    skill_obj.setdefault("sub_stats", {})
    skill_obj.setdefault("skill", {})
    skill_obj.setdefault("skill_cover", 0.8)

    def safe_float(v):
        try:
            return float(v) if v not in (None, "") else 0.0
        except:
            return 0.0

    # ---- 定义更新边际收益标签的函数 ----
    def _update_margin_label_ui():
        try:
            role_data = group_weapon._role_data
            # 复制角色数据，移除 weapon 字段（得到“不含弧盘”的配置）
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

    # 存储更新函数到 group_weapon，方便外部调用
    group_weapon._update_margin_label_ui = _update_margin_label_ui

    # 统一的数据变更处理函数
    def _on_data_changed():
        on_save_callback()
        # 更新弧盘自身的边际收益标签
        if hasattr(group_weapon, '_update_margin_label_ui'):
            group_weapon._update_margin_label_ui()
        # 刷新边际收益面板
        if on_margin_refresh_callback:
            on_margin_refresh_callback()

    # =========================
    # 1. 名称行（带选取按钮）
    # =========================
    name_row = QHBoxLayout()
    name_row.addWidget(QLabel("名称:"))

    name_edit = QLineEdit()
    name_edit.setText(weapon_data.get("name", ""))
    name_edit.textChanged.connect(_on_data_changed)
    name_row.addWidget(name_edit)

    # 弹性空间将后续内容推到右侧
    name_row.addStretch()

    # 边际收益标签
    margin_label = QLabel("直伤收益: 0.00%")
    margin_label.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 13px;")
    name_row.addWidget(margin_label)

    def _load_weapon_data():
        weapon_db = load_weapons()
        names = list(weapon_db.keys())
        if not names:
            QMessageBox.information(window, "提示", "weapon.json 中没有弧盘数据")
            return

        selected, ok = QInputDialog.getItem(window, "选择弧盘", "请选择弧盘：", names, 0, False)
        if not ok or not selected:
            return

        weapon_info = weapon_db[selected]

        mix_levels = weapon_info.get("mix_level_sub_stats", {})
        if mix_levels:
            level_keys = sorted(mix_levels.keys(), key=lambda x: int(x) if x.isdigit() else 0)
            if level_keys:
                level, ok = QInputDialog.getItem(window, "选择混频等级", "请选择混频等级（1~5）：", level_keys, 0, False)
                if not ok or not level:
                    return
                selected_mix = mix_levels[level]
            else:
                selected_mix = {}
        else:
            selected_mix = {}

        weapon_data["name"] = selected

        if "sub_stats" in weapon_info and isinstance(weapon_info["sub_stats"], dict):
            weapon_data["sub_stats"] = weapon_info["sub_stats"].copy()
        else:
            weapon_data["sub_stats"] = {}

        skill_obj = weapon_data["skill"]
        skill_obj["sub_stats"] = selected_mix.get("sub_stats", {}).copy()
        skill_obj["skill"] = selected_mix.get("skill", {}).copy()
        skill_obj["skill_cover"] = float(selected_mix.get("skill_cover", 0.8))

        # 刷新整个组
        _refresh_weapon_group(group_weapon)
        # 刷新边际收益
        if on_margin_refresh_callback:
            on_margin_refresh_callback()

    select_btn = QPushButton("选取弧盘")
    select_btn.setObjectName("btnAction")
    select_btn.clicked.connect(_load_weapon_data)
    name_row.addWidget(select_btn)

    layout.addLayout(name_row)

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

    # 攻击力白值
    white_spin = NoWheelDoubleSpinBox()
    white_spin.setRange(-999999, 999999)
    white_spin.setValue(float(base_info.get("攻击力白值", 300.0)))
    row1 = QHBoxLayout()
    row1.addWidget(QLabel("攻击力白值"))
    row1.addWidget(white_spin)
    layout.addLayout(row1)

    # 第二个属性
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
        _on_data_changed()  # ✅

    white_spin.editingFinished.connect(commit_base)
    combo2.currentTextChanged.connect(lambda _: commit_base())
    spin2.editingFinished.connect(commit_base)

    # =========================
    # 3. 技能加成
    # =========================
    skill_label = QLabel("技能加成：")
    skill_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
    layout.addWidget(skill_label)

    # ---------- 3.1 技能基础加成 ----------
    sb_label = QLabel("技能基础加成：")
    sb_label.setStyleSheet("font-weight:bold; color:#8bc34a;")
    layout.addWidget(sb_label)

    sb_container = QWidget()
    sb_layout = QVBoxLayout(sb_container)
    sb_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(sb_container)

    sb_rows = []

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
        _on_data_changed()  # ✅

    for k, v in skill_obj["sub_stats"].items():
        add_sb_row(k, v)

    sb_add_btn = QPushButton("+ 添加技能基础加成")
    sb_add_btn.clicked.connect(lambda: add_sb_row())
    layout.addWidget(sb_add_btn)

    # ---------- 3.2 技能具体加成 ----------
    ss_label = QLabel("技能具体加成：")
    ss_label.setStyleSheet("font-weight:bold; color:#8bc34a;")
    layout.addWidget(ss_label)

    ss_container = QWidget()
    ss_layout = QVBoxLayout(ss_container)
    ss_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(ss_container)

    ss_rows = []

    def add_ss_row(key="", value=0.0):
        row_widget = QWidget()
        row_layout = QHBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)

        line_edit = QLineEdit()
        line_edit.setText(key)
        line_edit.setPlaceholderText("输入属性名")

        spin = NoWheelDoubleSpinBox()
        spin.setRange(-999999, 999999)
        spin.setDecimals(2)
        spin.setValue(safe_float(value))

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
            if k:
                new_dict[k] = spin.value()
        skill_obj["skill"] = new_dict
        _on_data_changed()  # ✅

    for k, v in skill_obj["skill"].items():
        add_ss_row(k, v)

    ss_add_btn = QPushButton("+ 添加技能具体加成")
    ss_add_btn.clicked.connect(lambda: add_ss_row())
    layout.addWidget(ss_add_btn)

    # ---------- 3.3 技能覆盖率 ----------
    cover_spin = NoWheelDoubleSpinBox()
    cover_spin.setRange(0, 1.0)
    cover_spin.setSingleStep(0.05)
    cover_spin.setDecimals(2)
    cover_spin.setValue(float(skill_obj.get("skill_cover", 0.8)))

    def commit_cover():
        skill_obj["skill_cover"] = cover_spin.value()
        _on_data_changed()

    cover_spin.editingFinished.connect(commit_cover)

    row_cover = QHBoxLayout()
    row_cover.addWidget(QLabel("技能覆盖率"))
    row_cover.addWidget(cover_spin)
    layout.addLayout(row_cover)

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
