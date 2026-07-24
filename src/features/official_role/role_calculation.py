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



