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
from src.domain.stat_catalog import StatCatalog
from src.features.allocation import results_view as legacy_results
from src.features.inventory.warehouse import WarehouseResultCard, warehouse_item_view
from src.services.official_role_page_service import (
    calculate_official_role_damage_breakdown,
    calculate_official_role_equipment_gain,
    calculate_official_role_item_gain,
    calculate_official_role_margins,
    load_official_role_detail,
    load_official_role_index,
    replacement_candidates_for_official_role,
    save_official_role_replacement,
    save_official_role_tab_order,
)
from src.services.character_weight_service import save_account_character_weights
from src.services.official_equipment_bonus_service import calculate_official_equipment_stats
from src.services.sqlite_allocation_inventory import (
    AllocationInventoryProjectionError,
    legacy_shape_id,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from src.ui.equipment_replacement_dialog import (
    EquipmentReplacementCard,
    show_equipment_replacement_dialog,
)
from src.ui.persistent_tab_order import bind_persistent_tab_order
from src.ui.widgets import (
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
    match_pinyin,
)

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


def _graduation_benchmark_damage(detail: dict) -> float | None:
    """Calculate the strict-substat graduation reference through the official path."""

    template = _graduation_template_with_weight_substats(detail)
    if not isinstance(template, dict):
        return None
    equipment = template.get("equipment") or ()
    context = {
        "title": "毕业基准", "available": True, "items": equipment,
    }
    calculation_detail = {
        **detail,
        "profile": dict(template.get("profile") or detail.get("profile") or {}),
        "equipment_contexts": {
            **(detail.get("equipment_contexts") or {}), "graduation": context,
        },
    }
    damage = float(
        (calculate_official_role_margins(calculation_detail, "graduation") or {}).get("damage")
        or 0.0
    )
    return damage if damage > 0 else None


def _graduation_template_with_weight_substats(detail: dict) -> dict | None:
    """Project template gear onto the same strict pool exposed by 权重.

    Older static templates could choose an element-damage main-only attribute as
    a substat.  Preserve the stored core-main candidate/profile while deriving
    both module and core substats from ``stats.json`` at display time.
    """
    template = detail.get("graduation_template")
    if not isinstance(template, dict):
        return None
    if not isinstance(detail.get("property_weights"), dict):
        return dict(template)
    catalog = StatCatalog.from_config_dir(getattr(runtime, "CONFIG_DIR", "config"))
    labels_by_property = {property_id: label for label, property_id in _WEIGHT_PROPERTY_CHOICES}
    value_by_property = {
        property_id: label for property_id, label in labels_by_property.items()
        if label in catalog.tape_sub_stat_pool()
    }
    weights = {
        property_id: float((detail.get("property_weights") or {}).get(property_id, 0.0))
        for property_id in value_by_property
    }
    selected = sorted(weights, key=lambda key: (-weights[key], key))[:4]
    attributes = detail.get("attributes") or {}

    def stat(property_id: str, values: dict[str, float], multiplier: float) -> dict:
        label = value_by_property[property_id]
        percent = bool((attributes.get(property_id) or {}).get("show_percent"))
        raw_value = float(values[label]) * multiplier
        return {
            "property_id": property_id,
            "value": raw_value / 100.0 if percent else raw_value,
            "percent": percent,
        }

    equipment = [dict(item) for item in template.get("equipment") or ()]
    for item in equipment:
        if str(item.get("kind") or "") == "module":
            item["sub_stats"] = [
                stat(property_id, catalog.gold_base_values, float(template.get("drive_area") or 20))
                for property_id in selected
            ]
        elif str(item.get("kind") or "") == "core":
            item["sub_stats"] = [
                stat(property_id, catalog.tape_stat_values, 1.0)
                for property_id in selected
            ]
    return {**template, "equipment": equipment}


def _graduation_tooltip(detail: dict) -> str:
    """Describe the stored benchmark equipment behind the graduation percentage."""
    template = _graduation_template_with_weight_substats(detail) or {}
    equipment = template.get("equipment") or ()
    if not isinstance(equipment, (list, tuple)):
        return "毕业基准尚未生成。"
    core = next(
        (item for item in equipment if str(item.get("kind") or "") == "core"),
        {},
    )
    main = next(iter(core.get("main_stats") or ()), {})
    main_text = _stat_text(detail, main) if main else "未记录"
    aggregated_substats: dict[tuple[str, bool], dict] = {}
    for item in equipment:
        for stat in item.get("sub_stats") or ():
            key = (str(stat.get("property_id") or ""), bool(stat.get("percent")))
            if key not in aggregated_substats:
                aggregated_substats[key] = dict(stat)
                continue
            aggregated_substats[key]["value"] = (
                float(aggregated_substats[key].get("value") or 0.0)
                + float(stat.get("value") or 0.0)
            )
    substat_text = [
        _stat_text(detail, stat) for stat in aggregated_substats.values()
    ]
    substat_lines = [
        "、".join(substat_text[index:index + 3])
        for index in range(0, len(substat_text), 3)
    ]
    lines = [
        "毕业基准（满级角色、满级精1专属弧盘）：",
        f"卡带主词条：{main_text}",
        "毕业副词条：" + (substat_lines[0] if substat_lines else "未记录"),
    ]
    lines.extend(f"　　　　　{line}" for line in substat_lines[1:])
    return "\n".join(lines)


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


def _selected_growth(editor: dict) -> tuple[int, int] | None:
    """Resolve a typed level to the matching official growth stage."""
    growth = editor.get("growth")
    if growth is None:
        return None
    if hasattr(growth, "currentData"):
        current = growth.currentData()
        if current is not None:
            return int(current[0]), int(current[1])
    if not hasattr(growth, "value"):
        return None
    level = int(growth.value())
    candidates = [
        row for row in (editor.get("growth_rows") or ())
        if int(row.get("level") or 0) == level
    ]
    if not candidates:
        return None
    selected = max(candidates, key=lambda row: int(row.get("breakthrough_stage") or 0))
    return int(selected["level"]), int(selected["breakthrough_stage"])


def _calculation_detail(detail: dict, editor: dict) -> dict:
    """Project the unsaved editor state into a temporary calculation-only detail."""

    profile = dict(detail["profile"])
    growth = _selected_growth(editor)
    if growth is not None:
        level, breakthrough = growth
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
            int(editor["refinement"].currentData()) if fork_id else None
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
    graduation_label = QLabel("直伤毕业率 : --")
    graduation_label.setObjectName("officialRoleGraduationRate")
    graduation_label.setStyleSheet("font-weight:bold;color:#ffaa00;font-size:14px;")
    graduation_label.setToolTip(_graduation_tooltip(detail))
    header.addWidget(graduation_label)
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
        calculation_detail = _calculation_detail(detail, editor)
        margin_context = str(editor.get("equipment_context_key") or "current")
        margins = calculate_official_role_margins(
            calculation_detail, margin_context,
        )
        state["margins"] = margins
        _clear_layout(table_layout)
        damage = float((margins or {}).get("damage") or 0.0)
        benchmark = _graduation_benchmark_damage(calculation_detail)
        graduation_label.setToolTip(_graduation_tooltip(calculation_detail))
        graduation_label.setText(
            f"直伤毕业率 : {damage / benchmark * 100:.1f}%"
            if damage > 0 and benchmark else "直伤毕业率 : --"
        )
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
    icon_path = detail.get("icon_path")
    if icon_path:
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            avatar = QLabel()
            avatar.setObjectName("officialRoleBaseAvatar")
            avatar.setFixedSize(96, 96)
            avatar.setScaledContents(True)
            avatar.setPixmap(pixmap)
            left_layout.addWidget(avatar, alignment=Qt.AlignHCenter)
    role_name = QLabel(str(character.get("name_zh") or character_id))
    role_name.setAlignment(Qt.AlignHCenter)
    role_name.setStyleSheet("font-weight:bold;color:#58a6ff;")
    left_layout.addWidget(role_name)
    growth_combo = NoWheelSpinBox()
    growth_combo.setRange(
        min(int(row["level"]) for row in growth_rows),
        max(int(row["level"]) for row in growth_rows),
    )
    growth_combo.setValue(int(profile["character_level"]))
    growth_combo.setButtonSymbols(QSpinBox.NoButtons)
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
    awakening = NoWheelSpinBox()
    awakening.setRange(0, 6)
    awakening.setValue(int(profile["awakening_level"]))
    skill_combo = NoWheelComboBox()
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
        spin = NoWheelDoubleSpinBox()
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
        level = int(growth_combo.value())
        rows_for_level = [
            row for row in growth_rows if int(row["level"]) == level
        ]
        selected = max(
            rows_for_level,
            key=lambda row: int(row.get("breakthrough_stage") or 0),
            default={},
        )
        stat_values["hp_base"].setValue(float(selected.get("hp_base") or 0))
        stat_values["atk_base"].setValue(float(selected.get("atk_base") or 0))
        stat_values["def_base"].setValue(float(selected.get("def_base") or 0))
        stat_values["crit_rate"].setValue(5.0)
        stat_values["crit_damage"].setValue(50.0)

    update_stats()
    growth_combo.valueChanged.connect(update_stats)

    pointer_dialog = QDialog(window)
    pointer_dialog.setWindowTitle(f"{character.get('name_zh') or character_id} - 养成指针")
    pointer_dialog.resize(520, 240)
    pointer_layout = QVBoxLayout(pointer_dialog)
    pointer_form = QFormLayout()
    pointer_form.addRow("觉醒等级", awakening)
    pointer_form.addRow("直伤技能", skill_combo)
    skill_level = NoWheelSpinBox()
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
        "growth_rows": growth_rows,
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


def _fork_skill_description(star: dict) -> str:
    """Render official refinement placeholders with the selected level's curve values."""

    description = str(star.get("description_zh") or "")
    for parameter in star.get("parameters") or ():
        value = parameter.get("value")
        if value is None:
            continue
        number = float(value) * (100.0 if parameter.get("is_percent") else 1.0)
        shown = f"{number:.6f}".rstrip("0").rstrip(".")
        if parameter.get("is_percent"):
            shown += "%"
        description = description.replace(
            "{" + str(int(parameter.get("ordinal") or 0)) + "}",
            shown,
        )
    return description.replace("<lv>", "").replace("</>", "")


def _build_fork_group(window, character_id: int, detail: dict, editor: dict) -> QGroupBox:
    character = detail["character"]
    profile = detail["profile"]
    group = QGroupBox("弧盘加成")
    group.setObjectName("officialRoleForkGroup")
    layout = QVBoxLayout(group)
    identity = QHBoxLayout()
    identity.addWidget(QLabel("名称:"))
    fork_combo = NoWheelComboBox()
    fork_combo.setMaxVisibleItems(10)
    fork_combo.addItem("未装备弧盘", None)
    for fork in detail["forks"]:
        exclusive = str(character_id) in {str(value) for value in fork.get("exclusive_character_ids") or []}
        suffix = "（专属）" if exclusive else "（常驻同类型）"
        fork_combo.addItem(f"{fork.get('name_zh') or fork['fork_id']} {suffix}", fork["fork_id"])
    fork_index = fork_combo.findData(profile.get("fork_id"))
    fork_combo.setCurrentIndex(fork_index if fork_index >= 0 else 0)
    identity.addWidget(fork_combo, 1)
    fork_level = NoWheelSpinBox()
    fork_level.setRange(1, 80)
    fork_level.setValue(int(profile.get("fork_level") or 80))
    identity.addWidget(QLabel("等级:"))
    identity.addWidget(fork_level)
    refinement = NoWheelComboBox()
    refinement.setMaxVisibleItems(5)
    for level in range(1, 6):
        refinement.addItem(str(level), level)
    refinement_index = refinement.findData(int(profile.get("fork_refinement_level") or 1))
    refinement.setCurrentIndex(refinement_index if refinement_index >= 0 else 0)
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
    effect_label = QLabel("技能描述：")
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
            (row for row in star_rows if int(row.get("star_level") or 0) == refinement.currentData()),
            star_rows[0] if star_rows else None,
        )
        if star:
            description = _fork_skill_description(star)
            effect_text.setText(f"{star.get('title_zh') or ''}\n{description}".strip())
        else:
            effect_text.setText("暂无官方精炼说明。")

    fork_combo.currentIndexChanged.connect(refresh_fork_summary)
    fork_level.valueChanged.connect(refresh_fork_summary)
    refinement.currentIndexChanged.connect(refresh_fork_summary)
    refresh_fork_summary()
    editor.update({"fork": fork_combo, "fork_level": fork_level, "refinement": refinement})

    def mark_and_refresh(*_args) -> None:
        _mark_dirty(window, character_id)
        _refresh_role_calculations(editor)

    for widget in (fork_combo, fork_level, refinement):
        signal = getattr(widget, "currentIndexChanged", None) or widget.valueChanged
        signal.connect(mark_and_refresh)
    return group


def _equipment_item_card(
    window,
    detail: dict,
    item: dict,
    *,
    core: bool,
    score: float | None = None,
    direct_damage_score: float | None = None,
    replacement_callback=None,
) -> QWidget:
    view = warehouse_item_view(item)
    icon_path = detail.get("item_icon_paths", {}).get(
        str(item.get("item_id") or "")
    )
    if icon_path:
        view["item_icon_path"] = icon_path
    resolved_score = (
        _equipment_weight_score(window, detail, item, core=core)
        if score is None
        else float(score)
    )
    area = 15 if core else int(item.get("grid_count") or 0)
    if not core and area <= 0:
        geometry = str(item.get("geometry") or "")
        area = next(
            (
                int(character)
                for character in reversed(geometry)
                if character.isdigit()
            ),
            0,
        )
    return WarehouseResultCard(
        view,
        score=resolved_score,
        grade=legacy_results._calc_grade(window, resolved_score, area),
        direct_damage_score=direct_damage_score,
        split_metrics=True,
        replacement_callback=replacement_callback,
        parent=window if isinstance(window, QWidget) else None,
    )


def _equipment_weight_score(
    window,
    detail: dict,
    item: dict,
    *,
    core: bool,
) -> float:
    if not getattr(window, "scoring_engine", None):
        return 0.0
    weights = {
        _attribute_name(detail, str(property_id)): float(weight)
        for property_id, weight in (detail.get("property_weights") or {}).items()
    }
    main_weights = {
        _attribute_name(detail, str(property_id)): float(weight)
        for property_id, weight in (detail.get("main_property_weights") or {}).items()
    }
    sub_stats = {
        _attribute_name(detail, str(stat.get("property_id") or "")): float(
            stat.get("value") or 0.0
        )
        for stat in item.get("sub_stats") or ()
    }
    quality = {
        "orange": "Gold",
        "gold": "Gold",
        "purple": "Purple",
        "blue": "Blue",
    }.get(str(item.get("quality") or "").casefold(), "Gold")
    if core:
        main_stat = next(
            (
                _attribute_name(detail, str(stat.get("property_id") or ""))
                for stat in item.get("main_stats") or ()
            ),
            "",
        )
        return float(legacy_results._score_tape_dict(
            window,
            main_stat,
            sub_stats,
            weights,
            quality,
            main_weights,
        ))
    try:
        shape_id = legacy_shape_id(str(item.get("geometry") or ""))
    except AllocationInventoryProjectionError:
        shape_id = str(item.get("geometry") or "")
    return float(legacy_results._score_drive_dict(
        window,
        sub_stats,
        shape_id,
        weights,
        quality,
    ))


def _show_replacement_optimizer(window, detail: dict, target: dict) -> None:
    """Choose a SQLite inventory replacement for one saved-plan item."""

    candidates = replacement_candidates_for_official_role(detail, "saved", target)
    if not candidates:
        QMessageBox.information(
            window, "替换优化", "没有同套装、同形状且未被当前方案使用的可替换装备。",
        )
        return
    current_item = dict(candidates[0]["current_item"])

    def card_data(
        item: dict,
        *,
        direct_damage_score: float | None,
        payload,
    ) -> EquipmentReplacementCard:
        core = str(item.get("kind") or "") == "core"
        view = warehouse_item_view(item)
        icon_path = detail.get("item_icon_paths", {}).get(
            str(item.get("item_id") or "")
        )
        if icon_path:
            view["item_icon_path"] = icon_path
        score = _equipment_weight_score(window, detail, item, core=core)
        area = 15 if core else int(item.get("grid_count") or 0)
        return EquipmentReplacementCard(
            key=f"{item.get('uid_slot')}:{item.get('uid_serial')}",
            item_view=view,
            score=score,
            grade=legacy_results._calc_grade(window, score, area),
            direct_damage_score=direct_damage_score,
            payload=payload,
            note=(
                f"将从 {view.get('equipped_character_name')} 的持久化方案借用，"
                "并在同一事务中为其原槽位补入金色占位装备。"
                if view.get("equipped_character_name")
                else ""
            ),
        )

    current = card_data(
        current_item,
        direct_damage_score=candidates[0].get("current_direct_damage_score"),
        payload=None,
    )
    choices = [
        card_data(
            dict(row["item"]),
            direct_damage_score=row.get("direct_damage_score"),
            payload=row,
        )
        for row in candidates[:30]
    ]

    def save_choice(choice: EquipmentReplacementCard) -> None:
        row = choice.payload
        save_official_role_replacement(
            runtime.USER_DATABASE_PATH,
            detail,
            target,
            row["item"],
            score=float(row["damage"]),
        )

    accepted = show_equipment_replacement_dialog(
        window,
        title="替换优化",
        role_name=str((detail.get("character") or {}).get("name_zh") or ""),
        summary="所有卡片均按官方满级主属性计算；点击候选卡片比较，确认后写入 SQLite 配装方案。",
        current=current,
        candidates=choices,
        on_confirm=save_choice,
    )
    if accepted:
        refresh_equip = getattr(window, "_refresh_equip", None)
        if callable(refresh_equip):
            refresh_equip()
        _refresh_my_role(window)
        QMessageBox.information(window, "替换优化", "已保存为新的配装方案。")


def _build_equipment_cards_group(
    window, detail: dict, context_key: str,
) -> QGroupBox:
    context = detail["equipment_contexts"][context_key]
    theory_items: list[tuple[str, object]] = []
    items: list[dict] = []
    if context_key == "theory":
        core_id = context.get("core_item_id")
        modules = list((detail.get("equipment_plan") or {}).get("module_item_ids") or ())
        theory_items = (
            [("core", core_id)] if core_id else []
        ) + [("module", item_id) for item_id in modules]
        item_count = len(theory_items)
    else:
        items = list(context.get("items") or ())
        items.sort(key=lambda item: 0 if str(item.get("kind") or "") == "core" else 1)
        item_count = len(items)

    group = QGroupBox(f"空幕 / 驱动详情 ({item_count}件)")
    group.setObjectName("officialRoleEquipmentCards")
    layout = QVBoxLayout(group)
    layout.setSpacing(8)
    if context_key == "theory":
        layout.addWidget(QLabel(
            "官方推荐主属性：" + (
                "、".join(
                    _attribute_name(detail, property_id)
                    for property_id in context.get("core_main_property_ids") or ()
                ) or "未提供"
            )
        ))

    grid = QGridLayout()
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(10)
    if context_key == "theory":
        for index, (kind, item_id) in enumerate(theory_items):
            grid.addWidget(
                WarehouseResultCard(
                    {
                        "kind": kind,
                        "display_name": str(
                            detail.get("item_names", {}).get(item_id, item_id)
                            or ("空幕" if kind == "core" else "驱动")
                        ),
                        "item_name": str(item_id or ""),
                        "item_icon_path": detail.get("item_icon_paths", {}).get(
                            str(item_id or "")
                        ),
                        "quality": "gold",
                        "quality_color": "#e3a23b",
                        "level": 0,
                        "max_level": 0,
                        "level_known": False,
                        "main_stats": (),
                        "sub_stats": (),
                    },
                    score=None,
                    grade=None,
                    direct_damage_score=None,
                    parent=window if isinstance(window, QWidget) else None,
                ),
                index // 3,
                index % 3,
                Qt.AlignLeft | Qt.AlignTop,
            )
        if not theory_items:
            grid.addWidget(QLabel("官方方案未提供空幕或驱动。"), 0, 0)
    else:
        if not items:
            grid.addWidget(QLabel("暂无空幕或驱动。"), 0, 0)
        for index, item in enumerate(items):
            replacement_callback = None
            if context_key == "saved":
                replacement_callback = (
                    lambda target=dict(item): _show_replacement_optimizer(
                        window, detail, target,
                    )
                )
            gain = calculate_official_role_item_gain(detail, context_key, item)
            grid.addWidget(
                _equipment_item_card(
                    window,
                    detail,
                    item,
                    core=str(item.get("kind") or "") == "core",
                    direct_damage_score=(
                        float(gain["gain_percent"]) if gain else None
                    ),
                    replacement_callback=replacement_callback,
                ),
                index // 3,
                index % 3,
                Qt.AlignLeft | Qt.AlignTop,
            )
    grid.setColumnStretch(3, 1)
    layout.addLayout(grid)
    return group


def _aggregate_equipment_stats(detail: dict, context_key: str) -> list[tuple[str, str]]:
    if context_key == "theory":
        return [
            (_attribute_name(detail, property_id), "目标词条")
            for property_id in detail["equipment_contexts"]["theory"].get("property_ids") or ()
        ]
    property_percent = {
        str(property_id): bool(attribute.get("show_percent"))
        for property_id, attribute in (detail.get("attributes") or {}).items()
    }
    totals = calculate_official_equipment_stats(
        detail["equipment_contexts"][context_key].get("items") or (),
        property_percent=property_percent,
    )
    rows = []
    for total in totals:
        shown = total.value * 100 if total.percent else total.value
        text = f"+{shown:.2f}".rstrip("0").rstrip(".")
        if total.percent:
            text += "%"
        rows.append((_attribute_name(detail, total.property_id), text))
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
    context_combo = NoWheelComboBox()
    for key in ("current", "saved"):
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
        rows = _aggregate_equipment_stats(calculation_detail, context_key)
        if not rows:
            summary_layout.addWidget(QLabel("（暂无驱动/空幕，请先同步背包或保存配装方案）"))
        else:
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
        summary_layout.addWidget(
            _build_equipment_cards_group(window, calculation_detail, context_key)
        )

    def change_context() -> None:
        editor["equipment_context_key"] = str(context_combo.currentData())
        _refresh_role_calculations(editor)

    context_combo.currentIndexChanged.connect(change_context)
    _register_calculation_refresh(editor, refresh_summary)
    refresh_summary()
    return group


def _build_weight_group(
    window, character_id: int, detail: dict, editor: dict,
) -> QGroupBox:
    group = QGroupBox("词条权重")
    group.setObjectName("officialRoleWeightGroup")
    layout = QVBoxLayout(group)
    layout.setSpacing(8)

    editor_panel = QWidget()
    editor_layout = QVBoxLayout(editor_panel)
    editor_layout.setContentsMargins(0, 0, 0, 0)
    editor_layout.setSpacing(8)
    top = QHBoxLayout()
    top.addWidget(QLabel("词条权重:"))
    source = str(detail.get("property_weight_source") or "default")
    source_label = QLabel(
        "账号权重"
        if detail.get("property_weights_from_account")
        else f"推荐权重 · {source}"
    )
    source_label.setStyleSheet("color:#8b949e;font-size:11px;")
    top.addWidget(source_label)
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
        "优先使用当前账号权重；账号未配置时会以只读静态库的工坊推荐初始化。"
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
    form.addWidget(_build_base_group(window, character_id, detail, editor))
    form.addWidget(_build_margin_group(window, character_id, detail, editor))
    form.addWidget(_build_fork_group(window, character_id, detail, editor))
    form.addWidget(_build_drive_summary_group(window, detail, editor))
    form.addWidget(_build_damage_formula_group(detail, editor))
    form.addWidget(_build_weight_group(window, character_id, detail, editor))
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
                growth = _selected_growth(editor)
                if growth is None:
                    raise ValueError("角色等级不在官方成长数据范围内")
                fork_id = _selected_combo_data(editor["fork"])
                dao.save_character_profile(
                    character_id=character_id,
                    character_level=int(growth[0]),
                    breakthrough_stage=int(growth[1]),
                    awakening_level=editor["awakening"].value(),
                    fork_id=fork_id,
                    fork_level=editor["fork_level"].value() if fork_id else None,
                    fork_refinement_level=(
                        int(editor["refinement"].currentData() or 1)
                        if fork_id else None
                    ),
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
    tab_ids = {}
    for role in roles:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setProperty("loaded", False)
        character_id = int(role["character_id"])
        index = tabs.addTab(scroll, str(role.get("name_zh") or character_id))
        tabs.tabBar().setTabData(index, character_id)
        tab_ids[character_id] = index

    window._official_role_tab_order_binding = bind_persistent_tab_order(
        tabs,
        item_id_at=lambda index: int(tabs.tabBar().tabData(index)),
        save_order=lambda character_ids: save_official_role_tab_order(
            runtime.USER_DATABASE_PATH,
            tuple(int(character_id) for character_id in character_ids),
        ),
        on_error=lambda exc: QMessageBox.warning(
            window,
            "保存角色顺序失败",
            str(exc),
        ),
    )

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
