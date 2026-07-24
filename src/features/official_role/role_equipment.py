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


from . import role_calculation as _calculation
from . import role_growth as _growth
for _module in (_calculation, _growth):
    for _name, _value in vars(_module).items():
        if callable(_value) and not _name.startswith("__"):
            globals().setdefault(_name, _value)

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
        window._refresh_my_role()
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


