# 将角色编辑草稿投影为只读面板、直伤与权重展示，不承担保存或任务生命周期。
"""消费已物化的正式输入，复用 Service 计算口径，展示层不读取数据库。"""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QHeaderView

from src.domain.official_role.advancement import select_character_growth, select_fork_breakthrough
from src.services.official_role.graduation import graduation_rate, graduation_tooltip as _graduation_tooltip
from src.services.official_role.marginal_scoring import calculate_official_role_final_weights, calculate_official_role_margins
from src.services.official_role.panel_explanation import calculate_official_role_damage_breakdown


def _attribute_name(detail: dict, property_id: str) -> str:
    attribute = detail.get("attributes", {}).get(property_id, {})
    return str(attribute.get("display_name_zh") or attribute.get("filter_name_zh") or property_id)


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            widget = item.widget()
            widget.hide()
            # 延迟删除期间继续归原页面所有；解除 parent 会短暂成为原生顶层窗口，
            # Windows 在换页后处理 deleteLater 前可能因此闪出小弹窗。
            widget.deleteLater()
        if item.layout() is not None:
            _clear_layout(item.layout())
            item.layout().deleteLater()


def _mark_dirty(window, character_id: int) -> None:
    # 脏状态按正式角色 ID 隔离，中文名和别名不参与保存对象选择。
    window._official_role_dirty_ids.add(int(character_id))
    window._my_role_dirty = True


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
    breakthrough = editor.get("growth_breakthrough")
    preferred_stage = breakthrough.currentData() if breakthrough is not None else None
    selected = select_character_growth(
        editor.get("growth_rows") or (),
        level,
        preferred_stage=(
            int(preferred_stage) if preferred_stage is not None else None
        ),
    )
    if selected is None:
        return None
    return int(selected["level"]), int(selected["breakthrough_stage"])


def _selected_fork_stage(editor: dict) -> int | None:
    """Return the valid fork breakthrough stage for the unsaved editor state."""

    fork = editor.get("fork")
    fork_level = editor.get("fork_level")
    if fork is None or fork_level is None:
        return None
    fork_id = fork.currentData()
    if not fork_id:
        return None
    template = next(
        (
            row
            for row in (editor.get("detail") or {}).get("forks") or ()
            if row.get("fork_id") == fork_id
        ),
        None,
    )
    if template is None:
        return None
    selector = editor.get("fork_breakthrough")
    preferred_stage = selector.currentData() if selector is not None else None
    selected = select_fork_breakthrough(
        template.get("breakthroughs") or (),
        int(fork_level.value()),
        preferred_stage=(
            int(preferred_stage) if preferred_stage is not None else None
        ),
    )
    return int(selected["stage"]) if selected is not None else None


def _calculation_detail(detail: dict, editor: dict) -> dict:
    """Project the unsaved editor state into a temporary calculation-only detail."""

    # 只复制当前草稿为本次展示输入，不原地改冻结 detail，也不发起保存。
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
    profile["awakening_level"] = editor["awakening_level"].value()
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
        profile["fork_breakthrough_stage"] = (
            _selected_fork_stage(editor) if fork_id else None
        )
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
    graduation_label = QLabel("空幕直伤毕业率 : --")
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
        graduation_detail = {
            **calculation_detail,
            "property_weights": detail.get("property_weights") or {},
            "main_property_weights": detail.get("main_property_weights") or {},
        }
        rate = graduation_rate(graduation_detail, margin_context)
        graduation_label.setText(
            f"空幕直伤毕业率 : {rate:.1f}%" if rate is not None else "空幕直伤毕业率 : --"
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


# 权重表与直伤边际展示共用同一投影，不再维护独立只读转发模块。
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


def _build_weight_group(
    window, character_id: int, detail: dict, editor: dict,
) -> QGroupBox:
    group = QGroupBox("词条权重（只读）")
    group.setObjectName("officialRoleWeightGroup")
    layout = QVBoxLayout(group)
    layout.setSpacing(8)
    top = QHBoxLayout()
    top.addWidget(QLabel("当前面板权重:"))
    source_label = QLabel(
        "直伤公式词条按当前边际收益归一化；未参与直伤公式的词条保留基础权重"
    )
    source_label.setStyleSheet("color:#8b949e;font-size:11px;")
    top.addWidget(source_label)
    top.addStretch()
    layout.addLayout(top)

    table_host = QWidget()
    table_layout = QVBoxLayout(table_host)
    table_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(table_host, 1)

    def _format_weight(value: float) -> str:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")

    def rebuild() -> None:
        _clear_layout(table_layout)
        base_weights = {
            str(property_id): float(value)
            for property_id, value in (detail.get("property_weights") or {}).items()
        }
        final_weights = dict(
            editor.get("marginal_property_weights")
            or base_weights
            or {}
        )
        # 只有正式直伤公式参与项显示直伤权重；其它项保留基础口径，中文词条名只作标签。
        formula_ids = set(editor.get("formula_property_ids") or ())
        property_ids = set(base_weights) | set(final_weights) | formula_ids
        ordered_ids = sorted(
            property_ids,
            key=lambda property_id: (
                -float(final_weights.get(property_id, 0.0)),
                _WEIGHT_LABEL_BY_PROPERTY.get(
                    property_id, _attribute_name(detail, property_id)
                ),
                property_id,
            ),
        )
        table = QTableWidget(len(ordered_ids), 4)
        table.setObjectName("officialRoleWeightTable")
        table.setHorizontalHeaderLabels(["词条", "基础权重", "直伤权重", "最终权重"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for row_index, property_id in enumerate(ordered_ids):
            base_weight = float(base_weights.get(property_id, 0.0))
            final_weight = float(final_weights.get(property_id, base_weight))
            direct_weight = final_weight if property_id in formula_ids else 0.0
            table.setItem(
                row_index,
                0,
                QTableWidgetItem(
                    _WEIGHT_LABEL_BY_PROPERTY.get(
                        property_id, _attribute_name(detail, property_id)
                    )
                ),
            )
            table.setItem(row_index, 1, QTableWidgetItem(_format_weight(base_weight)))
            table.setItem(row_index, 2, QTableWidgetItem(_format_weight(direct_weight)))
            table.setItem(row_index, 3, QTableWidgetItem(_format_weight(final_weight)))
        for column in range(4):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
        table.setFixedHeight(
            table.horizontalHeader().height()
            + table.verticalHeader().defaultSectionSize() * len(ordered_ids)
            + table.frameWidth() * 2
        )
        table_layout.addWidget(table)
    editor["refresh_weights"] = rebuild
    rebuild()
    return group
