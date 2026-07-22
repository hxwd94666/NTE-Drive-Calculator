# 构建只读取官方静态库与账号 SQLite 指针的新角色页面。
"""Rebuilt character page using the old UI skeleton and official data sources."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QHeaderView

from src.app import runtime
from src.app.theme import themed_style
from src.services.official_role_page_service import (
    calculate_official_role_damage_breakdown,
    calculate_official_role_equipment_gain,
    calculate_official_role_item_gain,
    calculate_official_role_margins,
    load_official_role_detail,
    load_official_role_index,
)
from src.services.character_weight_service import save_account_character_weights
from src.services.sqlite_allocation_inventory import legacy_shape_id
from src.storage.sqlite.user_data_dao import UserDataDao
from src.ui.widgets import NoWheelDoubleSpinBox, match_pinyin

__all__ = ["_page_my_role", "_refresh_my_role", "confirm_pending_my_role_changes"]

_WEIGHT_PROPERTY_CHOICES = (
    ("暴击率%", "CritBase"),
    ("暴击伤害%", "CritDamageBase"),
    ("伤害增加%", "DamageUpGeneralBase"),
    ("攻击力%", "AtkUp"),
    ("攻击力", "AtkAdd"),
    ("防御力", "DefAdd"),
    ("防御力%", "DefUp"),
    ("生命值%", "HPMaxUp"),
    ("生命值", "HPMaxAdd"),
    ("环合强度", "MagBase"),
    ("倾陷强度", "UnbalIntensityBase"),
)
_WEIGHT_LABEL_BY_PROPERTY = {
    property_id: label for label, property_id in _WEIGHT_PROPERTY_CHOICES
}


def _attribute_name(detail: dict, property_id: str) -> str:
    attribute = detail.get("attributes", {}).get(property_id, {})
    return str(
        attribute.get("display_name_zh")
        or attribute.get("filter_name_zh")
        or property_id
    )


def _item_name(detail: dict, item: dict) -> str:
    return str(
        detail.get("item_names", {}).get(str(item.get("item_id") or ""))
        or (item.get("names") or {}).get("zh_cn")
        or item.get("item_id")
        or "未知装备"
    )


def _stat_text(detail: dict, stat: dict) -> str:
    value = float(stat.get("value") or 0.0)
    if stat.get("percent"):
        value *= 100.0
    shown = f"{value:.2f}".rstrip("0").rstrip(".")
    suffix = "%" if stat.get("percent") else ""
    return f"{_attribute_name(detail, str(stat.get('property_id') or ''))} {shown}{suffix}"


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            widget = item.widget()
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        if item.layout() is not None:
            _clear_layout(item.layout())
            item.layout().deleteLater()


def _mark_dirty(window, character_id: int) -> None:
    window._official_role_dirty_ids.add(int(character_id))
    window._my_role_dirty = True


def _selected_combo_data(combo: QComboBox):
    return combo.currentData()


def _calculation_detail(detail: dict, editor: dict) -> dict:
    """Project the unsaved editor state into a temporary calculation-only detail."""

    profile = dict(detail["profile"])
    growth = editor.get("growth")
    if growth is not None and growth.currentData() is not None:
        level, breakthrough = growth.currentData()
        profile["character_level"] = int(level)
        profile["breakthrough_stage"] = int(breakthrough)
    awakening = editor.get("awakening")
    if awakening is not None:
        profile["awakening_level"] = awakening.value()
    selected_skill = editor.get("selected_skill")
    if selected_skill is not None:
        profile["selected_skill_id"] = selected_skill.currentData()
    if editor.get("skill_levels") is not None:
        profile["skill_levels"] = dict(editor["skill_levels"])
    fork = editor.get("fork")
    if fork is not None:
        fork_id = fork.currentData()
        profile["fork_id"] = fork_id
        profile["fork_level"] = editor["fork_level"].value() if fork_id else None
        profile["fork_refinement_level"] = (
            editor["refinement"].value() if fork_id else None
        )
    return {
        **detail,
        "profile": profile,
        "property_weights": dict(editor.get("property_weights") or {}),
        "calculation_context_key": str(
            editor.get("equipment_context_key") or "current"
        ),
    }


def _register_calculation_refresh(editor: dict, callback) -> None:
    editor.setdefault("calculation_refreshers", []).append(callback)


def _refresh_role_calculations(editor: dict) -> None:
    if editor.get("refreshing_calculations"):
        return
    editor["refreshing_calculations"] = True
    try:
        for callback in tuple(editor.get("calculation_refreshers") or ()):
            callback()
    finally:
        editor["refreshing_calculations"] = False


def _equipment_items(detail: dict, context_key: str, *, core: bool) -> list[dict]:
    items = list(detail["equipment_contexts"][context_key].get("items") or ())
    if core:
        return [item for item in items if str(item.get("kind") or "") == "core"]
    return [item for item in items if str(item.get("kind") or "") != "core"]


def _build_margin_group(
    window, character_id: int, detail: dict, editor: dict,
) -> QGroupBox:
    group = QGroupBox("边际收益（按每单位收益排序）")
    group.setObjectName("officialRoleMarginalGroup")
    layout = QVBoxLayout(group)
    state = {"margins": None, "initialized": False}
    header = QHBoxLayout()
    damage_label = QLabel("直伤评分 : --")
    damage_label.setObjectName("officialRoleDamageScore")
    damage_label.setStyleSheet("font-weight:bold;color:#ffaa00;font-size:14px;")
    damage_label.setToolTip("使用当前官方角色指针和所选装备上下文计算。")
    header.addWidget(damage_label)
    header.addStretch()
    auto = QPushButton("自动设为权重")
    auto.setObjectName("btnPrimary")
    auto.setCheckable(True)
    auto.setChecked(True)
    header.addWidget(auto)
    apply = QPushButton("设为权重")
    apply.setObjectName("btnAction")
    header.addWidget(apply)
    layout.addLayout(header)
    table_host = QWidget()
    table_layout = QVBoxLayout(table_host)
    table_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(table_host)

    def apply_margin_weights(*, silent: bool) -> None:
        margins = state["margins"]
        if not margins:
            return
        weights = editor["property_weights"]
        updated = 0
        for row in margins["rows"]:
            property_id = str(row["property_id"])
            if property_id not in weights:
                continue
            weights[property_id] = round(float(row["gain_percent"]), 4)
            updated += 1
        if not updated:
            if not silent:
                QMessageBox.information(
                    window, "提示", "当前权重中没有与边际收益匹配的词条。",
                )
            return
        editor["weights_dirty"] = True
        _mark_dirty(window, character_id)
        refresh = editor.get("refresh_weights")
        if refresh:
            refresh()
        if not silent:
            QMessageBox.information(
                window, "成功", f"已更新 {updated} 个词条，请点击右上角保存。",
            )

    def refresh() -> None:
        margin_context = str(editor.get("equipment_context_key") or "current")
        margins = calculate_official_role_margins(
            _calculation_detail(detail, editor), margin_context,
        )
        state["margins"] = margins
        _clear_layout(table_layout)
        damage = float((margins or {}).get("damage") or 0.0)
        damage_label.setText(f"直伤评分 : {damage:.2f}" if margins else "直伤评分 : --")
        auto.setEnabled(bool(margins))
        apply.setEnabled(bool(margins))
        if not margins:
            note = QLabel("当前角色状态尚无可计算的官方直伤技能或装备上下文。")
            note.setWordWrap(True)
            table_layout.addWidget(note)
            state["initialized"] = True
            return
        table = QTableWidget(len(margins["rows"]), 4)
        table.setObjectName("officialRoleMarginalTable")
        table.setHorizontalHeaderLabels(["参数", "当前值", "1单位", "每单位提升"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for row_index, row in enumerate(margins["rows"]):
            is_percent = bool(row.get("is_percent"))
            current_value = float(row.get("current_value") or 0.0)
            unit_value = float(row.get("unit") or 0.0)
            current_text = (
                f"{current_value * 100:.2f}%" if is_percent else f"{current_value:.2f}"
            )
            unit_text = f"{unit_value * 100:g}%" if is_percent else f"{unit_value:g}"
            table.setItem(row_index, 0, QTableWidgetItem(row["label"]))
            table.setItem(row_index, 1, QTableWidgetItem(current_text))
            table.setItem(row_index, 2, QTableWidgetItem(unit_text))
            table.setItem(row_index, 3, QTableWidgetItem(f"{row['gain_percent']:.4f}%"))
        for column in range(4):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
        table.setFixedHeight(
            table.horizontalHeader().height()
            + table.verticalHeader().defaultSectionSize() * len(margins["rows"])
            + table.frameWidth() * 2
        )
        table_layout.addWidget(table)
        if state["initialized"] and auto.isChecked():
            apply_margin_weights(silent=True)
        state["initialized"] = True

    def toggle_auto(checked: bool) -> None:
        auto.setText("自动设为权重" if checked else "手动设为权重")
        auto.setObjectName("btnPrimary" if checked else "btnAction")
        auto.style().unpolish(auto)
        auto.style().polish(auto)
        if checked:
            apply_margin_weights(silent=True)

    auto.clicked.connect(toggle_auto)
    apply.clicked.connect(lambda: apply_margin_weights(silent=False))
    _register_calculation_refresh(editor, refresh)
    refresh()
    layout.addStretch()
    return group


def _populate_damage_formula_layout(layout, detail: dict) -> None:
    context_key = str(detail.get("calculation_context_key") or "current")
    context_title = detail["equipment_contexts"][context_key]["title"]
    breakdown = calculate_official_role_damage_breakdown(detail, context_key)
    if not breakdown:
        note = QLabel(f"计算上下文：{context_title}。当前没有可解释的直伤输入。")
        note.setWordWrap(True)
        layout.addWidget(note)
        return

    context_label = QLabel(
        f"计算上下文：{context_title} ｜ 技能倍率统一按 100% ｜ 百分比内部按小数参与计算"
    )
    context_label.setStyleSheet("color:#8b949e;")
    context_label.setWordWrap(True)
    layout.addWidget(context_label)

    bonus_title = QLabel("已有加成项目")
    bonus_title.setStyleSheet("font-weight:bold;color:#58a6ff;")
    layout.addWidget(bonus_title)
    bonuses = list(breakdown["bonuses"])
    bonus_table = QTableWidget(len(bonuses), 3)
    bonus_table.setObjectName("officialRoleDamageBonusTable")
    bonus_table.setHorizontalHeaderLabels(["来源", "项目", "数值"])
    bonus_table.setEditTriggers(QTableWidget.NoEditTriggers)
    bonus_table.setSelectionBehavior(QTableWidget.SelectRows)
    bonus_table.verticalHeader().setVisible(False)
    bonus_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    bonus_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    for row_index, bonus in enumerate(bonuses):
        value = float(bonus["value"])
        shown = f"{value * 100:.2f}%" if bonus.get("percent") else f"{value:.2f}"
        bonus_table.setItem(row_index, 0, QTableWidgetItem(str(bonus["source"])))
        bonus_table.setItem(row_index, 1, QTableWidgetItem(str(bonus["label"])))
        bonus_table.setItem(row_index, 2, QTableWidgetItem(shown))
    for column in range(3):
        bonus_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
    bonus_table.setFixedHeight(
        bonus_table.horizontalHeader().height()
        + bonus_table.verticalHeader().defaultSectionSize() * len(bonuses)
        + bonus_table.frameWidth() * 2
    )
    layout.addWidget(bonus_table)

    factor_title = QLabel("直伤乘区明细")
    factor_title.setStyleSheet("font-weight:bold;color:#58a6ff;")
    layout.addWidget(factor_title)
    factors = list(breakdown["factors"])
    factor_table = QTableWidget(len(factors), 3)
    factor_table.setObjectName("officialRoleDamageFactorTable")
    factor_table.setHorizontalHeaderLabels(["乘区", "结果", "组成"])
    factor_table.setEditTriggers(QTableWidget.NoEditTriggers)
    factor_table.setSelectionBehavior(QTableWidget.SelectRows)
    factor_table.verticalHeader().setVisible(False)
    factor_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    factor_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    for row_index, factor in enumerate(factors):
        value = float(factor["value"])
        shown = "100%" if row_index == 0 else (
            f"{value:.2f}" if row_index == 1 else f"× {value:.6f}"
        )
        factor_table.setItem(row_index, 0, QTableWidgetItem(str(factor["name"])))
        factor_table.setItem(row_index, 1, QTableWidgetItem(shown))
        factor_table.setItem(row_index, 2, QTableWidgetItem(str(factor["detail"])))
    factor_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    factor_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
    factor_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
    factor_table.setFixedHeight(
        factor_table.horizontalHeader().height()
        + factor_table.verticalHeader().defaultSectionSize() * len(factors)
        + factor_table.frameWidth() * 2
    )
    layout.addWidget(factor_table)

    values = [float(value) for value in breakdown["formula_values"]]
    expression = " × ".join(
        ["100%", f"{values[1]:.2f}", *(f"{value:.6f}" for value in values[2:])]
    )
    final_label = QLabel(f"最终直伤 = {expression} = {float(breakdown['damage']):.2f}")
    final_label.setObjectName("officialRoleDamageFormulaResult")
    final_label.setStyleSheet("font-weight:bold;color:#ffaa00;font-size:14px;")
    final_label.setWordWrap(True)
    final_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    layout.addWidget(final_label)


def _build_damage_formula_group(detail: dict, editor: dict) -> QGroupBox:
    group = QGroupBox("直伤公式详情")
    group.setObjectName("officialRoleDamageFormulaGroup")
    layout = QVBoxLayout(group)
    layout.setSpacing(8)

    def refresh() -> None:
        _clear_layout(layout)
        _populate_damage_formula_layout(layout, _calculation_detail(detail, editor))

    _register_calculation_refresh(editor, refresh)
    refresh()
    return group


def _build_base_group(window, character_id: int, detail: dict, editor: dict) -> QGroupBox:
    character = detail["character"]
    profile = detail["profile"]
    growth_rows = detail["growth_rows"]
    group = QGroupBox("基础加成")
    group.setObjectName("officialRoleBaseGroup")
    group.setStyleSheet("QGroupBox{font-weight:bold;}")
    layout = QVBoxLayout(group)
    content = QHBoxLayout()
    content.setSpacing(16)

    left = QWidget()
    left.setMinimumWidth(132)
    left.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    left_layout = QVBoxLayout(left)
    left_layout.setContentsMargins(0, 0, 0, 0)
    left_layout.setSpacing(8)
    left_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
    growth_combo = QComboBox()
    for row in growth_rows:
        growth_combo.addItem(
            str(row["level"]),
            (int(row["level"]), int(row["breakthrough_stage"])),
        )
    wanted_growth = (int(profile["character_level"]), int(profile["breakthrough_stage"]))
    index = growth_combo.findData(wanted_growth)
    growth_combo.setCurrentIndex(index if index >= 0 else max(0, growth_combo.count() - 1))
    level_row = QHBoxLayout()
    level_row.setSpacing(6)
    level_label = QLabel("等级:")
    level_label.setStyleSheet("font-weight:bold;color:#58a6ff;")
    level_row.addWidget(level_label)
    growth_combo.setFixedWidth(72)
    level_row.addWidget(growth_combo)
    help_button = QPushButton("?")
    help_button.setObjectName("btnHelp")
    help_button.setFixedSize(22, 22)
    help_button.setStyleSheet(
        "QPushButton#btnHelp{background:#58a6ff;color:white;border-radius:8px;font-weight:bold;"
        "font-size:10px;border:none;padding:0}QPushButton#btnHelp:hover{background:#1f6feb}"
    )
    level_row.addWidget(help_button)
    left_layout.addLayout(level_row)
    left_layout.addStretch()
    content.addWidget(left)

    right = QWidget()
    right_layout = QVBoxLayout(right)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(8)
    awakening = QSpinBox()
    awakening.setRange(0, 6)
    awakening.setValue(int(profile["awakening_level"]))
    skill_combo = QComboBox()
    for skill in detail["skills"]:
        skill_combo.addItem(str(skill["skill_id"]), skill["skill_id"])
    skill_index = skill_combo.findData(profile.get("selected_skill_id"))
    skill_combo.setCurrentIndex(skill_index if skill_index >= 0 else 0)

    stats_grid = QGridLayout()
    stats_grid.setHorizontalSpacing(14)
    stats_grid.setVerticalSpacing(8)
    stat_values = {}
    stat_specs = (
        ("生命白值", "hp_base"),
        ("攻击力白值", "atk_base"),
        ("防御力白值", "def_base"),
        ("暴击率%", "crit_rate"),
        ("暴击伤害%", "crit_damage"),
    )
    for stat_index, (label_text, key) in enumerate(stat_specs):
        grid_row = stat_index // 2
        grid_column = (stat_index % 2) * 2
        label = QLabel(label_text)
        label.setMinimumWidth(92)
        spin = QDoubleSpinBox()
        spin.setRange(-999999, 999999)
        spin.setDecimals(2)
        spin.setReadOnly(True)
        spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        spin.setMinimumWidth(110)
        stat_values[key] = spin
        stats_grid.addWidget(label, grid_row, grid_column)
        stats_grid.addWidget(spin, grid_row, grid_column + 1)
    stats_grid.setColumnStretch(1, 1)
    stats_grid.setColumnStretch(3, 1)
    right_layout.addLayout(stats_grid)
    content.addWidget(right, 1)
    layout.addLayout(content)

    def update_stats() -> None:
        wanted = growth_combo.currentData()
        selected = next(
            (
                row for row in growth_rows
                if (int(row["level"]), int(row["breakthrough_stage"])) == wanted
            ),
            {},
        )
        stat_values["hp_base"].setValue(float(selected.get("hp_base") or 0))
        stat_values["atk_base"].setValue(float(selected.get("atk_base") or 0))
        stat_values["def_base"].setValue(float(selected.get("def_base") or 0))
        stat_values["crit_rate"].setValue(5.0)
        stat_values["crit_damage"].setValue(50.0)

    update_stats()
    growth_combo.currentIndexChanged.connect(update_stats)

    pointer_dialog = QDialog(window)
    pointer_dialog.setWindowTitle(f"{character.get('name_zh') or character_id} - 养成指针")
    pointer_dialog.resize(520, 240)
    pointer_layout = QVBoxLayout(pointer_dialog)
    pointer_form = QFormLayout()
    pointer_form.addRow("觉醒等级", awakening)
    pointer_form.addRow("直伤技能", skill_combo)
    skill_level = QSpinBox()
    skill_level.setMinimum(1)
    pointer_form.addRow("技能等级", skill_level)
    pointer_layout.addLayout(pointer_form)
    pointer_note = QLabel("角色等级与突破在主页面左侧选择；其余技能等级会继续保留在账号数据库中。")
    pointer_note.setWordWrap(True)
    pointer_layout.addWidget(pointer_note)
    pointer_close = QPushButton("关闭")
    pointer_close.clicked.connect(pointer_dialog.accept)
    pointer_layout.addWidget(pointer_close)
    help_button.setToolTip("编辑觉醒和直伤技能")
    help_button.clicked.connect(pointer_dialog.exec)

    separator = QFrame()
    separator.setFrameShape(QFrame.HLine)
    separator.setFrameShadow(QFrame.Sunken)
    separator.setStyleSheet(themed_style("background-color:#30363d;max-height:1px"))
    layout.addWidget(separator)

    skills_by_id = {str(skill["skill_id"]): skill for skill in detail["skills"]}
    skill_levels = {str(key): int(value) for key, value in (profile.get("skill_levels") or {}).items()}
    skill_state = {"current": str(skill_combo.currentData() or "")}

    def refresh_skill_level() -> None:
        skill_id = str(skill_combo.currentData() or "")
        skill = skills_by_id.get(skill_id, {})
        levels = [int(row["level"]) for row in skill.get("levels") or ()]
        maximum = max(levels) if levels else 1
        skill_level.blockSignals(True)
        skill_level.setRange(1, maximum)
        skill_level.setValue(int(skill_levels.get(skill_id, maximum)))
        skill_level.blockSignals(False)
        skill_state["current"] = skill_id

    def commit_skill_level(value: int) -> None:
        skill_levels[skill_state["current"]] = int(value)
        _mark_dirty(window, character_id)
        _refresh_role_calculations(editor)

    refresh_skill_level()
    skill_combo.currentIndexChanged.connect(refresh_skill_level)
    skill_level.valueChanged.connect(commit_skill_level)

    editor.update({
        "growth": growth_combo,
        "awakening": awakening,
        "selected_skill": skill_combo,
        "skill_levels": skill_levels,
    })

    def mark_and_refresh(*_args) -> None:
        _mark_dirty(window, character_id)
        _refresh_role_calculations(editor)

    for widget in (growth_combo, awakening, skill_combo):
        signal = getattr(widget, "currentIndexChanged", None) or widget.valueChanged
        signal.connect(mark_and_refresh)
    return group


def _fork_stats(detail: dict, fork_id, level: int) -> dict[str, float]:
    fork = next((item for item in detail["forks"] if item.get("fork_id") == fork_id), None)
    if not fork:
        return {}
    upgrades = list(fork.get("upgrade_levels") or ())
    upgrade = min(upgrades, key=lambda row: abs(int(row.get("level") or 0) - level)) if upgrades else None
    breakthroughs = [
        row for row in fork.get("breakthroughs") or ()
        if int(row.get("max_fork_level") or 0) <= level
    ]
    breakthrough = max(breakthroughs, key=lambda row: int(row.get("stage") or 0)) if breakthroughs else None
    totals = {}
    for row in (upgrade, breakthrough):
        for modifier in (row or {}).get("modifiers") or ():
            property_id = str(modifier.get("property_id") or "")
            totals[property_id] = totals.get(property_id, 0.0) + float(modifier.get("value") or 0.0)
    return totals


def _display_property_value(detail: dict, property_id: str, value: float) -> str:
    attribute = detail.get("attributes", {}).get(property_id, {})
    if attribute.get("show_percent"):
        return f"+{value * 100:.2f}%".replace(".00%", "%")
    return f"+{value:.2f}".rstrip("0").rstrip(".")


def _build_fork_group(window, character_id: int, detail: dict, editor: dict) -> QGroupBox:
    character = detail["character"]
    profile = detail["profile"]
    group = QGroupBox("弧盘加成")
    group.setObjectName("officialRoleForkGroup")
    layout = QVBoxLayout(group)
    identity = QHBoxLayout()
    identity.addWidget(QLabel("名称:"))
    fork_combo = QComboBox()
    fork_combo.addItem("未装备弧盘", None)
    for fork in detail["forks"]:
        exclusive = str(character_id) in {str(value) for value in fork.get("exclusive_character_ids") or []}
        suffix = "（专属）" if exclusive else "（常驻同类型）"
        fork_combo.addItem(f"{fork.get('name_zh') or fork['fork_id']} {suffix}", fork["fork_id"])
    fork_index = fork_combo.findData(profile.get("fork_id"))
    fork_combo.setCurrentIndex(fork_index if fork_index >= 0 else 0)
    identity.addWidget(fork_combo, 1)
    fork_level = QSpinBox()
    fork_level.setRange(1, 80)
    fork_level.setValue(int(profile.get("fork_level") or 80))
    identity.addWidget(QLabel("等级:"))
    identity.addWidget(fork_level)
    refinement = QSpinBox()
    refinement.setRange(1, 5)
    refinement.setValue(int(profile.get("fork_refinement_level") or 1))
    identity.addWidget(QLabel("精炼:"))
    identity.addWidget(refinement)
    margin_label = QLabel("直伤收益: --")
    margin_label.setStyleSheet("color:#ffaa00;font-weight:bold;font-size:13px;")
    identity.addWidget(margin_label)
    layout.addLayout(identity)
    base_label = QLabel("基础加成：")
    base_label.setStyleSheet("font-weight:bold;color:#58a6ff;")
    layout.addWidget(base_label)
    stats_widget = QWidget()
    stats_layout = QVBoxLayout(stats_widget)
    stats_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(stats_widget)
    effect_label = QLabel("精炼效果：")
    effect_label.setStyleSheet("font-weight:bold;color:#58a6ff;")
    layout.addWidget(effect_label)
    effect_text = QLabel()
    effect_text.setWordWrap(True)
    effect_text.setMinimumHeight(72)
    layout.addWidget(effect_text)

    def refresh_fork_summary() -> None:
        _clear_layout(stats_layout)
        fork_id = fork_combo.currentData()
        level = fork_level.value()
        stats = _fork_stats(detail, fork_id, level)
        if not stats:
            stats_layout.addWidget(QLabel("未装备弧盘"))
        for property_id, value in stats.items():
            row = QHBoxLayout()
            row.addWidget(QLabel(_attribute_name(detail, property_id)))
            row.addStretch()
            shown = QLabel(_display_property_value(detail, property_id, value))
            shown.setStyleSheet("color:#58a6ff;font-weight:700;")
            row.addWidget(shown)
            stats_layout.addLayout(row)
        context_key = str(editor.get("equipment_context_key") or "current")
        calculation_detail = _calculation_detail(detail, editor)
        with_fork = {
            **calculation_detail,
            "profile": {
                **calculation_detail["profile"], "fork_id": fork_id, "fork_level": level,
            },
        }
        without_fork = {
            **calculation_detail,
            "profile": {**calculation_detail["profile"], "fork_id": None},
        }
        current = calculate_official_role_margins(with_fork, context_key)
        baseline = calculate_official_role_margins(without_fork, context_key)
        if current and baseline and baseline["damage"] > 0:
            gain = (current["damage"] / baseline["damage"] - 1.0) * 100.0
            margin_label.setText(f"直伤收益: {gain:+.2f}%")
        else:
            margin_label.setText("直伤收益: --")
        fork = next((item for item in detail["forks"] if item.get("fork_id") == fork_id), None)
        star_rows = list((fork or {}).get("star_levels") or ())
        star = next(
            (row for row in star_rows if int(row.get("star_level") or 0) == refinement.value()),
            star_rows[0] if star_rows else None,
        )
        if star:
            description = str(star.get("description_zh") or "").replace("<lv>", "").replace("</>", "")
            effect_text.setText(f"{star.get('title_zh') or ''}\n{description}".strip())
        else:
            effect_text.setText("暂无官方精炼说明。")

    fork_combo.currentIndexChanged.connect(refresh_fork_summary)
    fork_level.valueChanged.connect(refresh_fork_summary)
    refinement.valueChanged.connect(refresh_fork_summary)
    refresh_fork_summary()
    editor.update({"fork": fork_combo, "fork_level": fork_level, "refinement": refinement})

    def mark_and_refresh(*_args) -> None:
        _mark_dirty(window, character_id)
        _refresh_role_calculations(editor)

    for widget in (fork_combo, fork_level, refinement):
        signal = getattr(widget, "currentIndexChanged", None) or widget.valueChanged
        signal.connect(mark_and_refresh)
    return group


def _equipment_uid_text(item: dict) -> str:
    uid = item.get("uid") or {}
    if isinstance(uid, dict):
        return f"{int(uid.get('slot') or 0)}:{int(uid.get('serial') or 0)}"
    return str(uid or "")


def _legacy_quality_name(value: str | None) -> str:
    return {"orange": "Gold", "purple": "Purple", "blue": "Blue"}.get(
        str(value or "").lower(), "Gold"
    )


def _legacy_stat_values(detail: dict, stats) -> dict[str, float]:
    result = {}
    for stat in stats or ():
        value = float(stat.get("value") or 0.0)
        if stat.get("percent"):
            value *= 100.0
        result[_attribute_name(detail, str(stat.get("property_id") or ""))] = value
    return result


def _fallback_equipment_card(detail: dict, item: dict, *, core: bool) -> QWidget:
    card = QFrame()
    card.setObjectName("officialRoleEquipmentCard")
    card.setStyleSheet(themed_style(
        "QFrame#officialRoleEquipmentCard{background:#0d1117;border:1px solid #30363d;"
        "border-radius:10px;padding:10px}"
    ))
    layout = QVBoxLayout(card)
    title = _item_name(detail, item) if core else str(item.get("geometry") or "驱动").upper()
    heading = QLabel(f"<b>{title}</b>")
    heading.setStyleSheet("color:#4dd0e1;font-size:15px;")
    layout.addWidget(heading)
    stats = [*(item.get("main_stats") or ()), *(item.get("sub_stats") or ())]
    stat_label = QLabel(" · ".join(_stat_text(detail, stat) for stat in stats) or "无词条")
    stat_label.setWordWrap(True)
    layout.addWidget(stat_label)
    return card


def _old_style_equipment_card(window, detail: dict, item: dict, *, core: bool) -> QWidget:
    if not hasattr(window, "_equip_card"):
        return _fallback_equipment_card(detail, item, core=core)
    weights = {
        _attribute_name(detail, property_id): float(weight)
        for property_id, weight in (detail.get("property_weights") or {}).items()
    }
    quality = _legacy_quality_name(item.get("quality"))
    main_stats = list(item.get("main_stats") or ())
    sub_stats = _legacy_stat_values(detail, item.get("sub_stats") or ())
    uid = _equipment_uid_text(item)
    if core:
        label = _item_name(detail, item)
        main_stat = _stat_text(detail, main_stats[0]) if main_stats else ""
        icon_path = detail.get("item_icon_paths", {}).get(str(item.get("item_id") or ""))
        score_info = None
        if hasattr(window, "_score_tape_dict"):
            try:
                score = window._score_tape_dict(main_stat, sub_stats, weights, quality)
                grade = window._calc_grade(score, 15) if hasattr(window, "_calc_grade") else "-"
                score_info = (score, grade)
            except (TypeError, ValueError):
                score_info = None
        return window._equip_card(
            label, main_stat, sub_stats, None, uid, weights, score_info, quality,
            card_variant="inventory", item_icon_path=icon_path,
        )
    geometry = str(item.get("geometry") or "")
    shape_id = legacy_shape_id(geometry)
    label = geometry.upper() or "驱动"
    score_info = None
    if hasattr(window, "_score_drive_dict"):
        try:
            score = window._score_drive_dict(sub_stats, shape_id, weights, quality)
            area = getattr(window, "_shape_areas", {}).get(shape_id, 3)
            grade = window._calc_grade(score, area) if hasattr(window, "_calc_grade") else "-"
            score_info = (score, grade)
        except (TypeError, ValueError):
            score_info = None
    return window._equip_card(
        label, "", sub_stats, shape_id, uid, weights, score_info, quality,
        card_variant="inventory",
    )


def _build_equipment_detail_content(
    window, detail: dict, context_key: str,
) -> QWidget:
    content = QWidget()
    content.setObjectName("officialRoleEquipmentDetailContent")
    layout = QVBoxLayout(content)
    layout.setSpacing(10)
    context = detail["equipment_contexts"][context_key]

    summary = QGroupBox("空幕属性汇总")
    summary.setObjectName("officialRoleEquipmentDetailSummary")
    summary_layout = QVBoxLayout(summary)
    rows = _aggregate_equipment_stats(detail, context_key)
    if rows:
        for name, value in rows:
            row = QHBoxLayout()
            row.addWidget(QLabel(name))
            row.addStretch()
            shown = QLabel(value)
            shown.setStyleSheet("color:#58a6ff;font-weight:700;")
            row.addWidget(shown)
            summary_layout.addLayout(row)
    else:
        summary_layout.addWidget(QLabel("当前上下文暂无可汇总的数值词条。"))
    layout.addWidget(summary)

    if context_key == "theory":
        core_group = QGroupBox("空幕")
        core_layout = QVBoxLayout(core_group)
        core_id = context.get("core_item_id")
        core_layout.addWidget(QLabel(
            f"{detail.get('item_names', {}).get(core_id, core_id) or '未提供官方空幕'}"
        ))
        core_layout.addWidget(QLabel(
            "官方推荐主属性：" + (
                "、".join(
                    _attribute_name(detail, property_id)
                    for property_id in context.get("core_main_property_ids") or ()
                ) or "未提供"
            )
        ))
        layout.addWidget(core_group)
        modules = list((detail.get("equipment_plan") or {}).get("module_item_ids") or ())
        drive_group = QGroupBox(f"驱动 ({len(modules)}个)")
        drive_layout = QVBoxLayout(drive_group)
        for item_id in modules:
            drive_layout.addWidget(QLabel(
                str(detail.get("item_names", {}).get(item_id, item_id))
            ))
        if not modules:
            drive_layout.addWidget(QLabel("官方方案未提供驱动布局。"))
        layout.addWidget(drive_group)
        layout.addStretch()
        return content

    items = list(context.get("items") or ())
    cores = [item for item in items if str(item.get("kind") or "") == "core"]
    modules = [item for item in items if str(item.get("kind") or "") != "core"]
    core_group = QGroupBox("空幕")
    core_group.setObjectName("officialRoleEquipmentDetailCore")
    core_layout = QVBoxLayout(core_group)
    if not cores:
        core_layout.addWidget(QLabel("暂无空幕。"))
    for item in cores:
        core_layout.addWidget(_old_style_equipment_card(window, detail, item, core=True))
        gain = calculate_official_role_item_gain(detail, context_key, item)
        margin = QLabel(
            f"直伤收益: {gain['gain_percent']:+.2f}%" if gain else "直伤收益: --"
        )
        margin.setStyleSheet("color:#ffaa00;font-weight:bold;font-size:12px;")
        core_layout.addWidget(margin, alignment=Qt.AlignRight)
    layout.addWidget(core_group)

    drive_group = QGroupBox(f"驱动 ({len(modules)}个)")
    drive_group.setObjectName("officialRoleEquipmentDetailDrives")
    drive_layout = QVBoxLayout(drive_group)
    if not modules:
        drive_layout.addWidget(QLabel("暂无驱动。"))
    for item in modules:
        drive_layout.addWidget(_old_style_equipment_card(window, detail, item, core=False))
        gain = calculate_official_role_item_gain(detail, context_key, item)
        margin = QLabel(
            f"直伤收益: {gain['gain_percent']:+.2f}%" if gain else "直伤收益: --"
        )
        margin.setStyleSheet("color:#ffaa00;font-weight:bold;font-size:12px;")
        drive_layout.addWidget(margin, alignment=Qt.AlignRight)
    layout.addWidget(drive_group)
    layout.addStretch()
    return content


def _show_combined_equipment_details(
    window, detail: dict, initial_context: str = "current",
) -> None:
    dialog = QDialog(window)
    role_name = str((detail.get("character") or {}).get("name_zh") or "角色")
    dialog.setWindowTitle(f"{role_name} - 空幕 / 驱动详情")
    dialog.resize(1000, 700)
    root = QVBoxLayout(dialog)
    header = QHBoxLayout()
    header.addWidget(QLabel("装备方案："))
    selector = QComboBox()
    for key in ("current", "saved", "theory"):
        selector.addItem(detail["equipment_contexts"][key]["title"], key)
    index = selector.findData(initial_context)
    selector.setCurrentIndex(index if index >= 0 else 0)
    header.addWidget(selector)
    header.addStretch()
    root.addLayout(header)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    root.addWidget(scroll)

    def refresh() -> None:
        old = scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        scroll.setWidget(
            _build_equipment_detail_content(window, detail, str(selector.currentData()))
        )

    selector.currentIndexChanged.connect(refresh)
    refresh()
    close = QPushButton("关闭")
    close.clicked.connect(dialog.accept)
    root.addWidget(close)
    dialog.exec()


def _aggregate_equipment_stats(detail: dict, context_key: str) -> list[tuple[str, str]]:
    if context_key == "theory":
        return [
            (_attribute_name(detail, property_id), "目标词条")
            for property_id in detail["equipment_contexts"]["theory"].get("property_ids") or ()
        ]
    totals = {}
    percents = {}
    for item in detail["equipment_contexts"][context_key].get("items") or ():
        for stat in [*(item.get("main_stats") or ()), *(item.get("sub_stats") or ())]:
            property_id = str(stat.get("property_id") or "")
            totals[property_id] = totals.get(property_id, 0.0) + float(stat.get("value") or 0.0)
            percents[property_id] = bool(stat.get("percent"))
    rows = []
    for property_id, value in totals.items():
        shown = value * 100 if percents.get(property_id) else value
        text = f"+{shown:.2f}".rstrip("0").rstrip(".")
        if percents.get(property_id):
            text += "%"
        rows.append((_attribute_name(detail, property_id), text))
    return rows


def _build_drive_summary_group(window, detail: dict, editor: dict) -> QGroupBox:
    group = QGroupBox("空幕加成")
    group.setObjectName("officialRoleDriveGroup")
    layout = QVBoxLayout(group)
    layout.setSpacing(8)
    top = QHBoxLayout()
    count_label = QLabel()
    top.addWidget(count_label)
    top.addStretch()
    context_combo = QComboBox()
    for key in ("current", "saved", "theory"):
        context_combo.addItem(detail["equipment_contexts"][key]["title"], key)
    wanted_context = str(editor.get("equipment_context_key") or "current")
    context_index = context_combo.findData(wanted_context)
    context_combo.setCurrentIndex(context_index if context_index >= 0 else 0)
    context_combo.setFixedWidth(130)
    top.addWidget(context_combo)
    margin_label = QLabel("直伤收益: --")
    margin_label.setStyleSheet("color:#ffaa00;font-weight:bold;font-size:13px;")
    top.addWidget(margin_label)
    layout.addLayout(top)
    summary_host = QWidget()
    summary_layout = QVBoxLayout(summary_host)
    summary_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(summary_host)

    def refresh_summary() -> None:
        _clear_layout(summary_layout)
        context_key = str(context_combo.currentData())
        calculation_detail = _calculation_detail(detail, editor)
        modules = _equipment_items(detail, context_key, core=False) if context_key != "theory" else list((detail.get("equipment_plan") or {}).get("module_item_ids") or ())
        cores = _equipment_items(detail, context_key, core=True) if context_key != "theory" else ([1] if detail["equipment_contexts"]["theory"].get("core_item_id") else [])
        count_label.setText(f"已装配驱动: {len(modules)}    空幕: {'已装配' if cores else '未装配'}")
        gain = calculate_official_role_equipment_gain(calculation_detail, context_key)
        if gain:
            margin_label.setText(f"直伤收益: {gain['gain_percent']:+.2f}%")
        else:
            margin_label.setText("直伤收益: --")
        rows = _aggregate_equipment_stats(detail, context_key)
        if not rows:
            summary_layout.addWidget(QLabel("（暂无驱动/空幕，请先同步背包或保存配装方案）"))
            return
        info_group = QGroupBox("汇总属性（实时计算）")
        info_group.setStyleSheet(themed_style("QGroupBox{border:1px solid #30363d;border-radius:5px;padding:8px}"))
        info_layout = QVBoxLayout(info_group)
        for name, value in rows:
            row = QHBoxLayout()
            row.addWidget(QLabel(name))
            row.addStretch()
            label = QLabel(value)
            label.setStyleSheet("color:#58a6ff;font-weight:700;")
            row.addWidget(label)
            info_layout.addLayout(row)
        summary_layout.addWidget(info_group)

    def change_context() -> None:
        editor["equipment_context_key"] = str(context_combo.currentData())
        _refresh_role_calculations(editor)

    context_combo.currentIndexChanged.connect(change_context)
    _register_calculation_refresh(editor, refresh_summary)
    refresh_summary()
    details = QPushButton("查看空幕 / 驱动详情")
    details.setObjectName("btnSecondary")
    details.clicked.connect(
        lambda: _show_combined_equipment_details(
            window, _calculation_detail(detail, editor), str(context_combo.currentData())
        )
    )
    layout.addWidget(details)
    return group


def _build_weight_group(
    window, character_id: int, detail: dict, editor: dict,
) -> QGroupBox:
    group = QGroupBox("词条权重")
    group.setObjectName("officialRoleWeightGroup")
    layout = QHBoxLayout(group)
    layout.setSpacing(8)

    identity = QWidget()
    identity.setFixedWidth(132)
    identity_layout = QVBoxLayout(identity)
    identity_layout.setContentsMargins(0, 0, 0, 0)
    identity_layout.setSpacing(6)
    identity_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
    icon_path = detail.get("icon_path")
    if icon_path:
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            avatar = QLabel()
            avatar.setObjectName("officialRoleWeightAvatar")
            avatar.setFixedSize(88, 88)
            avatar.setScaledContents(True)
            avatar.setPixmap(pixmap)
            identity_layout.addWidget(avatar, alignment=Qt.AlignHCenter)
    role_name = QLabel(str(detail["character"].get("name_zh") or character_id))
    role_name.setAlignment(Qt.AlignHCenter)
    role_name.setStyleSheet("font-weight:bold;color:#58a6ff;")
    identity_layout.addWidget(role_name)
    source = str(detail.get("property_weight_source") or "default")
    source_label = QLabel("账号权重" if detail.get("property_weights_from_account") else f"推荐权重 · {source}")
    source_label.setAlignment(Qt.AlignHCenter)
    source_label.setStyleSheet("color:#8b949e;font-size:11px;")
    source_label.setWordWrap(True)
    identity_layout.addWidget(source_label)
    layout.addWidget(identity)

    editor_panel = QWidget()
    editor_layout = QVBoxLayout(editor_panel)
    editor_layout.setContentsMargins(0, 0, 0, 0)
    editor_layout.setSpacing(8)
    top = QHBoxLayout()
    top.addWidget(QLabel("词条权重:"))
    top.addStretch()
    add = QPushButton("+ 添加词条")
    add.setObjectName("btnAction")
    top.addWidget(add)
    editor_layout.addLayout(top)

    container = QWidget()
    container_layout = QVBoxLayout(container)
    container_layout.setContentsMargins(0, 0, 0, 0)
    container_layout.setSpacing(4)
    editor_layout.addWidget(container)
    layout.addWidget(editor_panel, 1)
    weights = editor["property_weights"]

    def changed() -> None:
        editor["weights_dirty"] = True
        _mark_dirty(window, character_id)
        _refresh_role_calculations(editor)

    def update_weight(property_id: str, value: float) -> None:
        weights[property_id] = float(value)
        changed()

    def rebuild() -> None:
        _clear_layout(container_layout)
        ordered_ids = sorted(
            weights,
            key=lambda property_id: (
                _WEIGHT_LABEL_BY_PROPERTY.get(
                    property_id, _attribute_name(detail, property_id)
                ),
                property_id,
            ),
        )
        for property_id in ordered_ids:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(QLabel(
                _WEIGHT_LABEL_BY_PROPERTY.get(
                    property_id, _attribute_name(detail, property_id)
                )
            ))
            spin = NoWheelDoubleSpinBox()
            spin.setRange(0, 10)
            spin.setSingleStep(0.05)
            spin.setDecimals(3)
            spin.setKeyboardTracking(False)
            spin.setValue(float(weights[property_id]))
            spin.valueChanged.connect(
                lambda value, pid=property_id: update_weight(pid, value)
            )
            row.addWidget(spin)
            remove = QPushButton("×")
            remove.setObjectName("btnSm")
            remove.setFixedSize(28, 28)

            def remove_weight(_checked=False, pid=property_id) -> None:
                weights.pop(pid, None)
                rebuild()
                changed()

            remove.clicked.connect(remove_weight)
            row.addWidget(remove)
            container_layout.addLayout(row)
        container_layout.addStretch()

    def add_weight() -> None:
        available = [
            (label, property_id)
            for label, property_id in _WEIGHT_PROPERTY_CHOICES
            if property_id not in weights
        ]
        if not available:
            QMessageBox.information(window, "提示", "所有词条已添加。")
            return
        labels = [label for label, _property_id in available]
        selected, accepted = QInputDialog.getItem(
            window, "添加词条", "选择词条:", labels, 0, False,
        )
        if not accepted:
            return
        property_id = dict(available).get(str(selected))
        if property_id:
            weights[property_id] = 0.5
            rebuild()
            changed()

    add.clicked.connect(add_weight)
    add.setToolTip(
        "优先使用当前账号权重；账号未配置时会复制只读静态库中的工坊推荐。"
    )
    editor["refresh_weights"] = rebuild
    rebuild()
    return group


def _populate_role_tab(window, scroll: QScrollArea, character_id: int) -> None:
    if scroll.property("loaded"):
        return
    detail = load_official_role_detail(runtime.USER_DATABASE_PATH, character_id)
    editor = {
        "detail": detail,
        "property_weights": dict(detail.get("property_weights") or {}),
        "weights_dirty": False,
        "equipment_context_key": (
            "saved" if detail["equipment_contexts"]["saved"]["available"] else "current"
        ),
    }
    window._official_role_editors[character_id] = editor
    content = QWidget()
    form = QVBoxLayout(content)
    form.setSpacing(15)
    form.setContentsMargins(15, 15, 15, 15)
    form.addWidget(_build_weight_group(window, character_id, detail, editor))
    form.addWidget(_build_margin_group(window, character_id, detail, editor))
    form.addWidget(_build_damage_formula_group(detail, editor))
    form.addWidget(_build_drive_summary_group(window, detail, editor))
    form.addWidget(_build_base_group(window, character_id, detail, editor))
    form.addWidget(_build_fork_group(window, character_id, detail, editor))
    form.addSpacing(100)
    form.addStretch()
    scroll.setWidget(content)
    scroll.setProperty("loaded", True)


def _save_profiles(window, *, show_message: bool = True) -> bool:
    dirty_ids = list(getattr(window, "_official_role_dirty_ids", set()))
    if not dirty_ids:
        if show_message:
            QMessageBox.information(window, "保存", "当前没有需要保存的角色修改。")
        return True
    weight_updates: list[tuple[int, dict[str, float]]] = []
    try:
        with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
            for character_id in dirty_ids:
                editor = window._official_role_editors.get(character_id)
                if not editor:
                    continue
                detail = editor["detail"]
                growth = _selected_combo_data(editor["growth"])
                fork_id = _selected_combo_data(editor["fork"])
                dao.save_character_profile(
                    character_id=character_id,
                    character_level=int(growth[0]),
                    breakthrough_stage=int(growth[1]),
                    awakening_level=editor["awakening"].value(),
                    fork_id=fork_id,
                    fork_level=editor["fork_level"].value() if fork_id else None,
                    fork_refinement_level=editor["refinement"].value() if fork_id else None,
                    selected_skill_id=_selected_combo_data(editor["selected_skill"]),
                    skill_levels=dict(editor["skill_levels"]),
                    ordinal=int(detail["profile"].get("ordinal") or 0),
                )
                if editor.get("weights_dirty"):
                    weight_updates.append((
                        character_id, dict(editor.get("property_weights") or {}),
                    ))
        for character_id, property_weights in weight_updates:
            save_account_character_weights(
                runtime.USER_DATABASE_PATH, character_id, property_weights,
            )
    except Exception as exc:
        QMessageBox.warning(window, "保存失败", str(exc))
        return False
    window._official_role_dirty_ids.clear()
    window._my_role_dirty = False
    if show_message:
        QMessageBox.information(window, "保存", "角色养成指针和词条权重已保存到当前账号数据库。")
    _refresh_my_role(window)
    return True


def _reset_current_role(window) -> None:
    tabs = getattr(window, "official_role_tabs", None)
    if tabs is None or tabs.currentIndex() < 0:
        return
    character_id = int(tabs.tabBar().tabData(tabs.currentIndex()))
    scroll = tabs.currentWidget()
    old = scroll.takeWidget()
    if old is not None:
        old.deleteLater()
    scroll.setProperty("loaded", False)
    window._official_role_editors.pop(character_id, None)
    window._official_role_dirty_ids.discard(character_id)
    window._my_role_dirty = bool(window._official_role_dirty_ids)
    _populate_role_tab(window, scroll, character_id)


def _page_my_role(window) -> QWidget:
    page = QWidget()
    root = QVBoxLayout(page)
    root.setContentsMargins(20, 16, 20, 16)
    root.setSpacing(10)
    page.setStyleSheet(themed_style(
        """
        QLabel{font-size:14px}
        QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox{font-size:14px;padding:8px 11px;border-radius:7px}
        QPushButton{font-size:13px;padding:8px 15px;border-radius:7px}
        QTabBar::tab{font-size:13px;padding:10px 20px}
        QGroupBox{font-size:15px;border:1px solid #30363d;border-radius:10px;padding:24px;padding-top:36px}
        """
    ))
    header = QHBoxLayout()
    header.addWidget(QLabel("编辑角色详情："))
    header.addStretch()
    reset = QPushButton("重置")
    reset.setObjectName("btnDanger")
    reset.setToolTip("放弃当前角色尚未保存的修改，重新读取账号数据库")
    reset.clicked.connect(lambda: _reset_current_role(window))
    save = QPushButton("保存")
    save.setObjectName("btnPrimary")
    save.clicked.connect(lambda: _save_profiles(window))
    header.addWidget(reset)
    header.addWidget(save)
    root.addLayout(header)

    area = QScrollArea()
    area.setWidgetResizable(True)
    content = QWidget()
    content_layout = QVBoxLayout(content)
    area.setWidget(content)
    root.addWidget(area, 1)
    window.my_role_form_area = area
    window.my_role_form_widget = content
    window.my_role_form_layout = content_layout
    window._official_role_page = page
    window._official_role_dirty_ids = set()
    window._official_role_editors = {}
    window._my_role_dirty = False
    _refresh_my_role(window)
    return page


def _refresh_my_role(window) -> None:
    layout = getattr(window, "my_role_form_layout", None)
    if layout is None:
        return
    current_id = getattr(window, "_current_official_role_id", None)
    _clear_layout(layout)
    window._official_role_editors = {}
    roles = load_official_role_index(runtime.USER_DATABASE_PATH)
    if not roles:
        layout.addWidget(QLabel("暂无官方角色数据。"))
        return

    search = QLineEdit()
    search.setObjectName("officialRoleSearch")
    search.setPlaceholderText("搜索角色（支持拼音）...")
    search.setClearButtonEnabled(True)
    search_row = QHBoxLayout()
    search_row.addWidget(search)
    search_row.addStretch()
    layout.addLayout(search_row)
    tabs = QTabWidget()
    tabs.setObjectName("officialRoleTabs")
    tabs.setMovable(True)
    tab_ids = {}
    for role in roles:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setProperty("loaded", False)
        character_id = int(role["character_id"])
        index = tabs.addTab(scroll, str(role.get("name_zh") or character_id))
        tabs.tabBar().setTabData(index, character_id)
        tab_ids[character_id] = index

    def load_visible(index: int) -> None:
        if index < 0:
            return
        character_id = int(tabs.tabBar().tabData(index))
        window._current_official_role_id = character_id
        _populate_role_tab(window, tabs.widget(index), character_id)

    def filter_tabs(text: str = "") -> None:
        keyword = text.strip()
        for index in range(tabs.count()):
            tabs.setTabVisible(index, not keyword or match_pinyin(tabs.tabText(index), keyword))

    tabs.currentChanged.connect(load_visible)
    search.textChanged.connect(filter_tabs)
    wanted_index = tab_ids.get(current_id, 0)
    tabs.setCurrentIndex(wanted_index)
    load_visible(tabs.currentIndex())
    window.official_role_search = search
    window.official_role_tabs = tabs
    layout.addWidget(tabs)


def confirm_pending_my_role_changes(window) -> bool:
    if not getattr(window, "_my_role_dirty", False):
        return True
    answer = QMessageBox.question(
        window,
        "未保存角色状态",
        "角色养成指针有未保存修改，是否先保存？",
        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        QMessageBox.Save,
    )
    if answer == QMessageBox.Cancel:
        return False
    if answer == QMessageBox.Save:
        return _save_profiles(window, show_message=False)
    window._official_role_dirty_ids.clear()
    window._my_role_dirty = False
    return True
