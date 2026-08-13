# 构建只读取官方静态库与账号 SQLite 指针的新角色页面。
"""Rebuilt character page using the old UI skeleton and official data sources."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.domain.allocation_rating import allocation_grade
from src.features.inventory.warehouse import warehouse_item_view
from src.features.inventory.warehouse_result_card import WarehouseResultCard
from src.services.official_role_equipment_scoring_service import (
    score_official_role_equipment,
)
from src.services.official_role_page_service import (
    calculate_official_role_equipment_gain,
    calculate_official_role_item_gain,
)
from src.services.official_equipment_bonus_service import calculate_official_equipment_stats
from src.ui.controllers.official_role_replacement_controller import (
    show_official_role_replacement,
)
from src.ui.widgets import (
    NoWheelComboBox,
)
from .role_calculation import (
    _attribute_name,
    _calculation_detail,
    _clear_layout,
    _equipment_items,
    _refresh_role_calculations,
    _register_calculation_refresh,
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
        grade=allocation_grade(resolved_score, area),
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
    return score_official_role_equipment(
        getattr(window, "scoring_engine", None),
        detail=detail,
        item=item,
        shape_areas=getattr(window, "_shape_areas", {}),
    )


def _show_replacement_optimizer(window, detail: dict, target: dict) -> None:
    """Open the shared replacement controller from the role page."""

    def refresh_after_save() -> None:
        tabs = getattr(window, "official_role_tabs", None)
        current_scroll = tabs.currentWidget() if tabs is not None else None
        restore_scroll_value = (
            current_scroll.verticalScrollBar().value()
            if isinstance(current_scroll, QScrollArea)
            else None
        )
        refresh_loadouts = getattr(
            window,
            "refresh_saved_equipment_after_mutation",
            None,
        )
        if callable(refresh_loadouts):
            refresh_loadouts()
        window._refresh_my_role(restore_scroll_value=restore_scroll_value)

    show_official_role_replacement(
        window,
        detail,
        target,
        on_saved=refresh_after_save,
    )


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
    calculation_by_uid = {
        (int(item.get("uid_slot") or 0), int(item.get("uid_serial") or 0)): item
        for item in context.get("calculation_items") or ()
        if int(item.get("uid_slot") or 0) or int(item.get("uid_serial") or 0)
    } if context_key != "theory" else {}

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
            display_item = calculation_by_uid.get(
                (int(item.get("uid_slot") or 0), int(item.get("uid_serial") or 0)),
                item,
            )
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
                    display_item,
                    core=str(display_item.get("kind") or "") == "core",
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
    shape_bonus = detail.get("shape_bonus") or {}
    context = detail["equipment_contexts"][context_key]
    totals = calculate_official_equipment_stats(
        context.get("calculation_items", context.get("items") or ()),
        extra_shape_label=str(shape_bonus.get("shape_label") or ""),
        extra_shape_buffs=tuple(
            (
                str(row.get("property_id") or ""),
                float(row.get("display_value") or 0.0),
            )
            for row in shape_bonus.get("properties") or ()
            if str(row.get("property_id") or "")
        ),
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
