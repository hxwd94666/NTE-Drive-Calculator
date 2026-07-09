# 构建角色套装技能加成编辑组件。
"""套装加成组件：编辑卡带套装技能和覆盖率。"""

from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QInputDialog, QVBoxLayout

from src.ui.widgets import NoWheelDoubleSpinBox, SearchableComboBox
from .dao import load_stats, load_tapes


def clear_layout(layout):
    """递归清除布局中的所有子项。"""
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
    group_tape = QGroupBox("套装加成")
    tape_layout = QVBoxLayout(group_tape)
    tape_layout.setSpacing(8)

    group_tape._window = window
    group_tape._role_name = role_name
    group_tape._role_data = role_data
    group_tape._on_save_callback = on_save_callback
    group_tape._on_margin_refresh_callback = on_margin_refresh_callback
    group_tape._on_update_nested_field = on_update_nested_field

    _build_tape_group_content(group_tape)
    parent_layout.addWidget(group_tape)
    return group_tape


def _build_tape_group_content(group_tape):
    clear_layout(group_tape.layout())
    layout = group_tape.layout()

    window = group_tape._window
    role_name = group_tape._role_name
    role_data = group_tape._role_data
    on_save_callback = group_tape._on_save_callback
    on_margin_refresh_callback = group_tape._on_margin_refresh_callback
    on_update_nested_field = group_tape._on_update_nested_field

    stats = load_stats()
    tape_pool = stats.get("tape_stat_values", {}) or {"攻击力%": 0.0}
    set_bonus = role_data.get("set_bonus")
    if not isinstance(set_bonus, dict):
        set_bonus = {"display_name": "", "skill": {}, "skill_2": {}, "skill_cover": 0.8}
        role_data["set_bonus"] = set_bonus

    for key in ("skill", "skill_2"):
        if not isinstance(set_bonus.get(key), dict):
            set_bonus[key] = {}
    set_bonus.setdefault("skill_cover", 0.8)

    def _on_data_changed():
        if on_save_callback:
            on_save_callback()
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
        _on_data_changed()

    title_row = QHBoxLayout()
    title_row.addWidget(QLabel(f"当前套装: {set_bonus.get('display_name') or '未设置'}"))
    title_row.addStretch()

    def _load_set_bonus():
        tapes_db = load_tapes()
        names = list(tapes_db.keys())
        if not names:
            return
        selected, ok = QInputDialog.getItem(window, "选择套装", "请选择套装：", names, 0, False)
        if not ok or not selected:
            return
        template = tapes_db[selected] or {}
        role_data["set_bonus"] = {
            "display_name": template.get("display_name", selected),
            "skill": template.get("skill", {}) or {},
            "skill_2": template.get("skill_2", {}) or {},
            "skill_cover": float(template.get("skill_cover", 0.8)),
        }
        _on_data_changed()
        _refresh_tape_group(group_tape)

    select_btn = QPushButton("选取套装")
    select_btn.setObjectName("btnAction")
    select_btn.clicked.connect(_load_set_bonus)
    title_row.addWidget(select_btn)
    layout.addLayout(title_row)

    def normalize_skill_dict(skill_dict):
        if not skill_dict:
            first_key = next(iter(tape_pool.keys()))
            skill_dict[first_key] = 0.0
            return
        if len(skill_dict) > 1:
            first_key = next(iter(skill_dict))
            first_value = skill_dict[first_key]
            skill_dict.clear()
            skill_dict[first_key] = first_value
        first_key = next(iter(skill_dict))
        if first_key not in tape_pool:
            tape_pool[first_key] = 0.0

    normalize_skill_dict(set_bonus["skill"])
    normalize_skill_dict(set_bonus["skill_2"])

    def create_skill_row(skill_key: str, label_text: str):
        layout.addWidget(QLabel(label_text))
        skill_dict = set_bonus[skill_key]
        stat_key = next(iter(skill_dict.keys()))
        stat_value = float(skill_dict[stat_key])

        row = QHBoxLayout()
        combo = SearchableComboBox()
        combo.addItems(list(tape_pool.keys()))
        combo.setCurrentText(stat_key)

        spin = NoWheelDoubleSpinBox()
        spin.setRange(-999999, 999999)
        spin.setDecimals(2)
        spin.setValue(stat_value)

        def commit():
            key = combo.currentText()
            value = spin.value()
            set_bonus[skill_key] = {key: value}
            _update_nested_field(["set_bonus", skill_key], set_bonus[skill_key])

        combo.currentTextChanged.connect(lambda _text: commit())
        spin.editingFinished.connect(commit)

        row.addWidget(combo)
        row.addWidget(spin)
        layout.addLayout(row)

    create_skill_row("skill", "技能1：")
    create_skill_row("skill_2", "技能2：")

    cover_row = QHBoxLayout()
    cover_row.addWidget(QLabel("技能2覆盖率:"))
    cover_spin = NoWheelDoubleSpinBox()
    cover_spin.setRange(0, 1)
    cover_spin.setSingleStep(0.05)
    cover_spin.setDecimals(2)
    cover_spin.setValue(float(set_bonus.get("skill_cover", 0.8)))
    cover_spin.editingFinished.connect(
        lambda: _update_nested_field(["set_bonus", "skill_cover"], cover_spin.value())
    )
    cover_row.addWidget(cover_spin)
    cover_row.addStretch()
    layout.addLayout(cover_row)


def _refresh_tape_group(group_tape):
    window = group_tape._window
    role_name = group_tape._role_name
    role_data = window._my_role_form_data.get(role_name, {})
    group_tape._role_data = role_data
    _build_tape_group_content(group_tape)


def refresh_tape_group(window, role_name: str):
    if not hasattr(window, "_tape_groups"):
        return
    group_tape = window._tape_groups.get(role_name)
    if group_tape:
        _refresh_tape_group(group_tape)
