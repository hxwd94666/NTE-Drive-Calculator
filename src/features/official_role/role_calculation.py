# 构建只读取官方静态库与账号 SQLite 指针的新角色页面。
"""Rebuilt character page using the old UI skeleton and official data sources."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QHeaderView

from src.services.official_role_graduation_service import (
    graduation_benchmark_damage,
    graduation_tooltip as _graduation_tooltip,
)
from src.services.official_role_page_service import (
    calculate_official_role_final_weights,
    calculate_official_role_damage_breakdown,
    calculate_official_role_margins,
)

__all__ = ["_page_my_role", "_refresh_my_role", "confirm_pending_my_role_changes"]

def _attribute_name(detail: dict, property_id: str) -> str:
    attribute = detail.get("attributes", {}).get(property_id, {})
    return str(attribute.get("display_name_zh") or attribute.get("filter_name_zh") or property_id)


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
    candidates = [row for row in (editor.get("growth_rows") or ()) if int(row.get("level") or 0) == level]
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
    awakening_checks = editor.get("awakening_checks") or {}
    selected_awaken_effect_ids = [
        effect_id
        for effect_id, check in awakening_checks.items()
        if check.isChecked()
    ]
    profile["awakening_level"] = len(selected_awaken_effect_ids)
    profile["selected_awaken_effect_ids"] = selected_awaken_effect_ids
    profile["awakening_selection_initialized"] = True
    likeability = editor.get("likeability_level_10")
    if likeability is not None:
        profile["likeability_level_10_enabled"] = likeability.isChecked()
    if editor.get("skill_levels") is not None:
        profile["skill_levels"] = dict(editor["skill_levels"])
    fork = editor.get("fork")
    if fork is not None:
        fork_id = fork.currentData()
        profile["fork_id"] = fork_id
        profile["fork_level"] = editor["fork_level"].value() if fork_id else None
        profile["fork_refinement_level"] = int(editor["refinement"].currentData()) if fork_id else None
    return {
        **detail,
        "profile": profile,
        "property_weights": dict(editor.get("marginal_property_weights") or detail.get("property_weights") or {}),
        "main_property_weights": dict(
            editor.get("marginal_main_property_weights") or detail.get("main_property_weights") or {}
        ),
        "calculation_context_key": str(editor.get("equipment_context_key") or "current"),
    }


def normalized_marginal_weights(
    base_weights: dict[str, float],
    margins: dict | None,
) -> tuple[dict[str, float], frozenset[str]]:
    """Return read-only role weights derived from the current direct-damage panel.

    Formula-participating properties always use their measured marginal gain:
    the largest positive one is 1.0 and an actual zero stays 0.  Only
    properties absent from the direct-damage formula retain their public
    official recommendation weight.
    """

    weights = {str(key): float(value) for key, value in (base_weights or {}).items()}
    rows = list((margins or {}).get("rows") or ())
    formula_ids = frozenset(str(row.get("property_id") or "") for row in rows) - {""}
    positive_gains = [
        float(row.get("gain_percent") or 0.0)
        for row in rows
        if math.isfinite(float(row.get("gain_percent") or 0.0)) and float(row.get("gain_percent") or 0.0) > 0.0
    ]
    maximum_gain = max(positive_gains, default=0.0)
    for row in rows:
        property_id = str(row.get("property_id") or "")
        if not property_id:
            continue
        gain = float(row.get("gain_percent") or 0.0)
        weights[property_id] = max(0.0, gain) / maximum_gain if maximum_gain > 0.0 and math.isfinite(gain) else 0.0
    return weights, formula_ids


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
    window,
    character_id: int,
    detail: dict,
    editor: dict,
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
    layout.addLayout(header)
    table_host = QWidget()
    table_layout = QVBoxLayout(table_host)
    table_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(table_host)
    graduation_benchmark = graduation_benchmark_damage(detail)

    def refresh() -> None:
        calculation_detail = _calculation_detail(detail, editor)
        margin_context = str(editor.get("equipment_context_key") or "current")
        margins = calculate_official_role_margins(
            calculation_detail,
            margin_context,
        )
        state["margins"] = margins
        final_weights = calculate_official_role_final_weights(
            calculation_detail,
            margin_context,
            margins=margins,
            base_property_weights=detail.get("property_weights") or {},
            base_main_property_weights=detail.get("main_property_weights") or {},
        )
        editor["marginal_property_weights"] = final_weights["property_weights"]
        editor["marginal_main_property_weights"] = final_weights["main_property_weights"]
        editor["formula_property_ids"] = final_weights["formula_property_ids"]
        refresh_weights = editor.get("refresh_weights")
        if refresh_weights:
            refresh_weights()
        _clear_layout(table_layout)
        damage = float((margins or {}).get("damage") or 0.0)
        graduation_label.setText(
            f"直伤毕业率 : {damage / graduation_benchmark * 100:.1f}%"
            if damage > 0 and graduation_benchmark else "直伤毕业率 : --"
        )
        damage_label.setText(f"直伤评分 : {damage:.2f}" if margins else "直伤评分 : --")
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
            current_text = f"{current_value * 100:.2f}%" if is_percent else f"{current_value:.2f}"
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
        state["initialized"] = True

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

    context_label = QLabel(f"计算上下文：{context_title} ｜ 技能倍率统一按 100% ｜ 百分比内部按小数参与计算")
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
        shown = "100%" if row_index == 0 else (f"{value:.2f}" if row_index == 1 else f"× {value:.6f}")
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
    expression = " × ".join(["100%", f"{values[1]:.2f}", *(f"{value:.6f}" for value in values[2:])])
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
