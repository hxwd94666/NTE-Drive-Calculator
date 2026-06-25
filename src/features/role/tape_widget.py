"""空幕相关 UI 组件"""

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
from .dao import load_stats, load_tapes
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


def build_tape_group(
    parent_layout,
    window,
    role_name: str,
    role_data: dict,
    on_save_callback,
    on_margin_refresh_callback=None,
    on_update_nested_field=None,
):
    """
    构建空幕加成 QGroupBox 并添加到 parent_layout
    """
    group_tape = QGroupBox("空幕加成")
    tape_layout = QVBoxLayout(group_tape)
    tape_layout.setSpacing(8)

    # 存储必要信息以便刷新
    group_tape._window = window
    group_tape._role_name = role_name
    group_tape._role_data = role_data
    group_tape._on_save_callback = on_save_callback
    group_tape._on_margin_refresh_callback = on_margin_refresh_callback
    group_tape._on_update_nested_field = on_update_nested_field

    # 构建内容
    _build_tape_group_content(group_tape)

    parent_layout.addWidget(group_tape)
    return group_tape


def _build_tape_group_content(group_tape):
    """构建空幕组的内容（可被刷新复用）"""
    # 使用 clear_layout 彻底清除所有内容（包括嵌套布局）
    clear_layout(group_tape.layout())
    layout = group_tape.layout()

    window = group_tape._window
    role_name = group_tape._role_name
    role_data = group_tape._role_data
    on_save_callback = group_tape._on_save_callback
    on_margin_refresh_callback = group_tape._on_margin_refresh_callback
    on_update_nested_field = group_tape._on_update_nested_field

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

    def safe_float(v):
        try:
            return float(v) if v not in (None, "") else 0.0
        except:
            return 0.0

    # ---- 定义更新边际收益标签的函数 ----
    def _update_margin_label_ui():
        try:
            # 复制角色数据，移除 tape 字段（得到“不含空幕”的配置）
            no_tape_data = {k: v for k, v in role_data.items() if k != "tape"}
            stats_without = get_character_total_stats(no_tape_data)
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
            print(f"计算空幕边际收益失败: {e}")

    group_tape._update_margin_label_ui = _update_margin_label_ui

    # 统一的数据变更处理函数
    def _on_data_changed():
        on_save_callback()
        # 更新空幕自身的边际收益标签
        if hasattr(group_tape, '_update_margin_label_ui'):
            group_tape._update_margin_label_ui()
        # 刷新边际收益面板
        if on_margin_refresh_callback:
            on_margin_refresh_callback()

    def _update_nested_field(path, value):
        if on_update_nested_field:
            on_update_nested_field(window, role_name, path, value)
        else:
            obj = role_data
            for key in path[:-1]:
                obj = obj.setdefault(key, {})
            obj[path[-1]] = value

    # =========================
    # 显示名 + 选取按钮 + 边际收益标签
    # =========================
    name_row = QHBoxLayout()
    name_row.addWidget(QLabel("显示名:"))

    name_edit = QLineEdit()
    name_edit.setText(tape_data.get("display_name", "空幕"))
    name_edit.editingFinished.connect(
        lambda: _update_nested_field(["tape", "display_name"], name_edit.text())
    )
    name_row.addWidget(name_edit)

    # 弹性空间
    name_row.addStretch()

    # 边际收益标签
    margin_label = QLabel("直伤收益: 0.00%")
    margin_label.setStyleSheet("color: #ffaa00; font-weight: bold; font-size: 13px;")
    name_row.addWidget(margin_label)

    # ---------- 选取空幕按钮 ----------
    def _load_tape_data():
        tapes_db = load_tapes()
        names = list(tapes_db.keys())
        if not names:
            QMessageBox.information(window, "提示", "tapes.json 中没有空幕数据")
            return

        selected, ok = QInputDialog.getItem(window, "选择空幕", "请选择空幕：", names, 0, False)
        if not ok or not selected:
            return

        tape_info = tapes_db[selected]

        new_display = tape_info.get("display_name", selected)
        tape_data["display_name"] = new_display
        name_edit.setText(new_display)

        if "skill" in tape_info and isinstance(tape_info["skill"], dict) and tape_info["skill"]:
            first_key = next(iter(tape_info["skill"]))
            tape_data["skill"] = {first_key: tape_info["skill"][first_key]}
        else:
            tape_data["skill"] = {}

        if "skill_2" in tape_info and isinstance(tape_info["skill_2"], dict) and tape_info["skill_2"]:
            first_key = next(iter(tape_info["skill_2"]))
            tape_data["skill_2"] = {first_key: tape_info["skill_2"][first_key]}
        else:
            tape_data["skill_2"] = {}

        tape_data["skill_cover"] = float(tape_info.get("skill_cover", 0.8))

        # 刷新整个组
        _refresh_tape_group(group_tape)
        # 刷新边际收益
        if on_margin_refresh_callback:
            on_margin_refresh_callback()

    select_btn = QPushButton("选取空幕")
    select_btn.setObjectName("btnAction")
    select_btn.clicked.connect(_load_tape_data)
    name_row.addWidget(select_btn)

    layout.addLayout(name_row)

    # =========================
    # 主词条
    # =========================
    main_label = QLabel("主词条（属性名 + 数值）：")
    main_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
    layout.addWidget(main_label)

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
        _on_data_changed()

    def _on_main_combo_changed():
        key = main_combo.currentText().strip()
        if key and key in tape_main_pool:
            main_spin.setValue(tape_main_pool[key])
        else:
            main_spin.setValue(0.0)
        commit_main()

    main_combo.currentTextChanged.connect(_on_main_combo_changed)
    main_spin.editingFinished.connect(commit_main)

    main_row.addWidget(main_combo)
    main_row.addWidget(main_spin)
    layout.addLayout(main_row)

    # =========================
    # 副属性（最多4条）
    # =========================
    sub_label = QLabel("副属性（最多4条，留空表示未设置）：")
    sub_label.setStyleSheet("font-weight:bold; color:#58a6ff;")
    layout.addWidget(sub_label)

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
        layout.addLayout(row)
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
        _on_data_changed()

    for combo, spin in sub_widgets:
        combo.currentTextChanged.connect(lambda _, c=combo, sp=spin: commit_sub_stats())
        spin.editingFinished.connect(commit_sub_stats)

    # =========================
    # skill_cover
    # =========================
    def _add_single_value_row(label_text, path, default=0.0, is_float=True):
        row = QHBoxLayout()
        row.addWidget(QLabel(label_text))
        widget = NoWheelDoubleSpinBox()
        widget.setRange(-999999, 999999)
        widget.setDecimals(2 if is_float else 0)
        widget.setValue(float(default))
        widget.editingFinished.connect(
            lambda: _update_nested_field(path, widget.value() if is_float else int(widget.value()))
        )
        row.addWidget(widget)
        row.addStretch()
        layout.addLayout(row)

    _add_single_value_row(
        "技能2覆盖率:",
        ["tape", "skill_cover"],
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
        layout.addWidget(row_label)

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
            _on_data_changed()

        combo.currentTextChanged.connect(lambda _: commit())
        spin.editingFinished.connect(commit)

        row.addWidget(combo)
        row.addWidget(spin)
        layout.addLayout(row)

    create_single_skill_row(tape_skill, "技能1：")
    create_single_skill_row(tape_skill2, "技能2：")

    # 初始更新边际收益标签
    _update_margin_label_ui()


def _refresh_tape_group(group_tape):
    """刷新空幕组内容（内部使用）"""
    window = group_tape._window
    role_name = group_tape._role_name
    role_data = window._my_role_form_data.get(role_name, {})
    group_tape._role_data = role_data
    _build_tape_group_content(group_tape)


def refresh_tape_group(window, role_name: str):
    """外部刷新空幕组"""
    if not hasattr(window, "_tape_groups"):
        return
    group_tape = window._tape_groups.get(role_name)
    if group_tape:
        _refresh_tape_group(group_tape)