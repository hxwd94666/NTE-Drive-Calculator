# 提供只含角色优先级、计算和统一结果的词条配装页面。
"""Minimal role-priority UI for the audited weighted-allocation facade."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QFormLayout, QGridLayout, QScrollArea, QVBoxLayout, QWidget,
)

from src.app import runtime
from src.app.theme import theme_color, theme_rgba, themed_style
from src.app.workers import WorkerThread
from src.features.allocation.priority_groups import priority_groups_to_links
from src.features.allocation.role_selector import RoleSelector, resolve_priority_choice
from src.features.allocation import results_view as legacy_results
from src.features.inventory import page as inventory_page
from src.features.inventory.warehouse import WarehouseResultCard, warehouse_item_view
from src.features.weighted_allocation.runner import (
    WeightedAllocationPersistence, WeightedAllocationPreview, WeightedAllocationRequest,
    read_weighted_allocation_persistence, restore_weighted_allocation_preview,
    replace_weighted_allocation_assignment, run_weighted_allocation,
    save_weighted_allocation_preview,
)
from src.services.allocation_solver import AllocationSolveResult, RoleAllocationOption
from src.services.allocation_context import AllocationContext
from src.services.account_settings_service import AccountSettingsService
from src.services.character_weight_service import (
    ensure_account_character_weights, save_account_character_weights,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.equipment_level_projection_service import (
    project_equipment_items_to_max_level,
)
from src.services.official_role_page_service import (
    calculate_official_role_attribute_summaries,
    calculate_official_role_item_gain,
    calculate_official_role_margins,
    load_official_role_detail,
)
from src.services.sqlite_allocation_inventory import (
    AllocationInventoryProjectionError, legacy_shape_id,
)
from src.services.virtual_equipment_service import (
    virtual_equipment_inventory_item,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.ui.attribute_summary_panel import (
    AttributeSummaryLoadout,
    AttributeSummaryPanel,
    AttributeSummaryRow,
)
from src.ui.equipment_replacement_dialog import (
    EquipmentReplacementCard,
    show_equipment_replacement_dialog,
)
from src.ui.puzzle_board import PuzzleBoardWidget
from src.ui.widgets import NoWheelDoubleSpinBox, SearchableComboBox


_INTERNAL_PROFILE_NAME = "__weighted_allocation_role_priority__"
# 普通入口不展示候选；避免为不可见的 Top-K 重复执行昂贵的 DFS 与评分。
_INTERNAL_TOP_K = 1

_MAIN_PROPERTY_CHOICES = (
    ("生命值百分比", "HPMaxUp"), ("攻击力百分比", "AtkUp"),
    ("防御力百分比", "DefUp"), ("暴击率", "CritBase"),
    ("暴击伤害", "CritDamageBase"), ("环合强度", "MagBase"),
    ("倾陷强度", "UnbalIntensityBase"), ("治疗加成", "HealUp"),
    ("光属性异能伤害增强", "DamageUpCosmosBase"),
    ("灵属性异能伤害增强", "DamageUpNatureBase"),
    ("咒属性异能伤害增强", "DamageUpIncantationBase"),
    ("暗属性异能伤害增强", "DamageUpChaosBase"),
    ("魂属性异能伤害增强", "DamageUpPsycheBase"),
    ("相属性异能伤害增强", "DamageUpLakshanaBase"),
    ("心灵伤害增强", "DamageUpPsychicallyBase"),
)
_SUBSTAT_PROPERTY_CHOICES = (
    ("暴击率%", "CritBase"), ("暴击伤害%", "CritDamageBase"),
    ("伤害增加%", "DamageUpGeneralBase"), ("攻击力%", "AtkUp"),
    ("攻击力", "AtkAdd"), ("防御力", "DefAdd"), ("防御力%", "DefUp"),
    ("生命值%", "HPMaxUp"), ("生命值", "HPMaxAdd"),
    ("环合强度", "MagBase"), ("倾陷强度", "UnbalIntensityBase"),
)
_RESULT_PROPERTY_LABELS = {property_id: label for label, property_id in _SUBSTAT_PROPERTY_CHOICES}
_RESULT_PROPERTY_LABELS.update({
    property_id: f"{label}%" if "伤害增强" in label or "治疗加成" in label else label
    for label, property_id in _MAIN_PROPERTY_CHOICES
    if property_id not in _RESULT_PROPERTY_LABELS
})
def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        if item.layout() is not None:
            _clear_layout(item.layout())
            item.layout().deleteLater()




def _request_weighted_equipment(*args, **kwargs):
    from .weighted_workflow import _request_weighted_equipment as request
    return request(*args, **kwargs)


def _request_weighted_replacement(*args, **kwargs):
    from .weighted_workflow import _request_weighted_replacement as request
    return request(*args, **kwargs)
def render_weighted_allocation_result(
    window,
    result: AllocationSolveResult,
    context: AllocationContext | None = None,
    *,
    role_details: Mapping[int, Mapping[str, Any]] | None = None,
) -> None:
    _clear_layout(window.weighted_result_layout)
    card = window._card("计算结果")
    card_layout = card.layout()
    window._weighted_role_equip_buttons = []
    candidates = {candidate.uid: candidate for candidate in (context.candidates if context else ())}
    role_preferences = {role.character_id: role for role in (context.roles if context else ())}
    shape_resources = _shape_resource_ids(context)
    # One role result needs the same official detail for its summary and its
    # per-item direct-damage scores.  Keep it for this immutable result rather
    # than reopening SQLite twice while building the same card.
    detail_cache: dict[int, Mapping[str, Any] | None] = dict(role_details or {})
    card_layout.addWidget(_LazyWeightedRoleCards(
        window,
        tuple(result.unified.selected),
        candidates,
        role_preferences,
        shape_resources,
        detail_cache,
        parent=card,
    ))
    if result.unified.unassigned_character_ids:
        card_layout.addWidget(QLabel(_unassigned_reason(window, context, result.unified.unassigned_character_ids)))
    window.weighted_result_layout.addWidget(card)


class _LazyWeightedRoleCards(QWidget):
    """Create result cards only near the visible page viewport.

    A full role card owns a puzzle widget, an attribute summary and up to eight
    equipment widgets.  Keeping placeholders for off-screen roles avoids a
    long main-thread stall after the solver finishes while preserving the
    existing result order and per-role actions.
    """

    _PREFETCH_PIXELS = 360
    _PLACEHOLDER_HEIGHT = 260

    def __init__(
        self,
        window,
        options: tuple[RoleAllocationOption, ...],
        candidates: Mapping[tuple[int, int], Any],
        roles: Mapping[int, Any],
        shape_resources: Mapping[str, str],
        detail_cache: dict[int, Mapping[str, Any] | None],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._candidates = candidates
        self._roles = roles
        self._shape_resources = shape_resources
        self._detail_cache = detail_cache
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        self._pending: dict[QWidget, RoleAllocationOption] = {}
        self._initial_load = True
        for option in options:
            placeholder = QFrame(self)
            placeholder.setMinimumHeight(self._PLACEHOLDER_HEIGHT)
            placeholder.setStyleSheet(themed_style(
                "QFrame{border:1px solid #30363d;border-radius:10px;background:#0d1117}"
            ))
            placeholder_layout = QVBoxLayout(placeholder)
            name = getattr(window, "_weighted_role_names", {}).get(
                option.character_id, "角色"
            )
            placeholder_layout.addWidget(QLabel(f"正在准备 {name} 的结果…"))
            placeholder_layout.addStretch()
            self._layout.addWidget(placeholder)
            self._pending[placeholder] = option
        page_scroll = getattr(window, "weighted_page_scroll", None)
        self._page_scroll = page_scroll if isinstance(page_scroll, QScrollArea) else None
        if self._page_scroll is not None:
            self._page_scroll.verticalScrollBar().valueChanged.connect(
                self._load_visible_cards
            )
        QTimer.singleShot(0, self._load_visible_cards)

    def _load_visible_cards(self, *_args) -> None:
        if not self._pending:
            return
        if self._page_scroll is None:
            targets = tuple(self._pending)
        else:
            viewport = self._page_scroll.viewport()
            targets = tuple(
                placeholder
                for placeholder in self._pending
                if self._is_near_viewport(placeholder, viewport)
            )
            # Before the layout has been shown Qt can report no geometry.  The
            # first card must still appear so the user gets an immediate result.
            if not targets and self._initial_load:
                targets = (next(iter(self._pending)),)
        self._initial_load = False
        if not targets:
            return
        for placeholder in targets:
            option = self._pending.pop(placeholder, None)
            if option is None:
                continue
            index = self._layout.indexOf(placeholder)
            self._layout.removeWidget(placeholder)
            placeholder.deleteLater()
            self._layout.insertWidget(index, _role_option_card(
                self._window,
                option,
                dict(self._candidates),
                self._roles.get(option.character_id),
                dict(self._shape_resources),
                self._detail_cache,
            ))
        if self._pending:
            QTimer.singleShot(0, self._load_visible_cards)

    def _is_near_viewport(self, placeholder: QWidget, viewport: QWidget) -> bool:
        top = viewport.mapFromGlobal(placeholder.mapToGlobal(QPoint(0, 0))).y()
        bottom = top + max(placeholder.height(), self._PLACEHOLDER_HEIGHT)
        return (
            bottom >= -self._PREFETCH_PIXELS
            and top <= viewport.height() + self._PREFETCH_PIXELS
        )


def _role_option_card(
    window, option: RoleAllocationOption, candidates: dict = None, role=None,
    shape_resources: dict[str, str] | None = None,
    detail_cache: dict[int, Mapping[str, Any] | None] | None = None,
) -> QWidget:
    name = getattr(window, "_weighted_role_names", {}).get(option.character_id, "角色")
    card = QGroupBox()
    card.setStyleSheet(themed_style(
        "QGroupBox{background:#0d1117;border:1px solid #30363d;"
        "border-radius:10px;margin-top:12px;padding:18px}"
    ))
    layout = QVBoxLayout(card)
    layout.setSpacing(10)
    core = next((item for item in option.assignments if item.kind == "core"), None)
    modules = [item for item in option.assignments if item.kind == "module"]
    grade = legacy_results._calc_grade(window, option.score, 35)
    grade_color = getattr(legacy_results, "GRADE_COLORS", {}).get(grade, "#58a6ff")
    role_header = QHBoxLayout()
    role_header.setSpacing(8)
    role_label = QLabel(name)
    role_label.setStyleSheet(
        f"font-size:15px;font-weight:800;color:{theme_color('#4dd0e1')};"
        f"border:1px solid {theme_color('#4dd0e1')};border-radius:7px;"
        f"padding:4px 14px;background:{theme_rgba('#4dd0e1', 0.10)}"
    )
    role_header.addWidget(role_label)
    role_header.addStretch()
    role_header.addWidget(_result_badge("评分", f"{option.score:.1f}", grade_color))
    role_header.addWidget(_result_badge("评级", grade, grade_color))
    equip_button = QPushButton("装配")
    equip_button.setObjectName("btnPrimary")
    equip_button.setEnabled(bool(getattr(window, "_weighted_equipment_actions_available", False)))
    equip_button.clicked.connect(
        lambda _checked=False, current_name=name: _request_weighted_equipment(
            window, mode="configured", role_name=current_name,
        )
    )
    window._weighted_role_equip_buttons.append(equip_button)
    role_header.addWidget(equip_button)
    layout.addLayout(role_header)
    layout.addSpacing(6)

    candidate_map = candidates or {}
    summary_core = candidate_map.get(core.uid) if core is not None else None
    summary_drives = [
        candidate
        for assignment in modules
        if (candidate := candidate_map.get(assignment.uid)) is not None
    ]
    detail = _weighted_result_role_detail(window, option.character_id, detail_cache)
    summary_panel = _official_bonus_summary_panel(
        window,
        name,
        option.character_id,
        summary_core,
        summary_drives,
        role,
        detail,
    )
    if option.generated_board:
        layout.addWidget(legacy_results._section_label(window, "拼图图纸:"))
        board_row = QHBoxLayout()
        board_row.setSpacing(18)
        board_row.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        board_row.addWidget(PuzzleBoardWidget([list(row) for row in option.generated_board]), 0, Qt.AlignTop)
        if summary_panel is not None:
            board_row.addWidget(summary_panel, 1, Qt.AlignTop)
        layout.addLayout(board_row)
        layout.addSpacing(8)
    elif summary_panel is not None:
        layout.addWidget(summary_panel)
    weights = dict(getattr(role, "effective_property_weights", ()) if role else ())
    main_weights = dict(
        getattr(role, "effective_main_property_weights", ()) if role else ()
    )
    if core is None:
        layout.addWidget(QLabel(_missing_core_text(window, role)))
    equipment_assignments = ([core] if core is not None else []) + modules
    if equipment_assignments:
        direct_damage_scores = _allocation_direct_damage_scores(
            window,
            option,
            candidate_map,
            detail=detail,
        )
        layout.addWidget(
            legacy_results._section_label(
                window, f"空幕 / 驱动 ({len(equipment_assignments)}件):"
            )
        )
        equipment_grid = QGridLayout()
        equipment_grid.setHorizontalSpacing(10)
        equipment_grid.setVerticalSpacing(10)
        for index, assignment in enumerate(equipment_assignments):
            equipment_grid.addWidget(
                _result_equipment_card(
                    window,
                    assignment,
                    candidates or {},
                    weights,
                    main_weights,
                    shape_resources or {},
                    replacement_callback=lambda current=assignment: _request_weighted_replacement(
                        window, name, current, role,
                    ),
                    direct_damage_score=direct_damage_scores.get(assignment.uid),
                ),
                index // 4,
                index % 4,
                Qt.AlignLeft | Qt.AlignTop,
            )
        equipment_grid.setColumnStretch(4, 1)
        layout.addLayout(equipment_grid)
    return card


def _weighted_result_role_detail(
    window,
    character_id: int,
    detail_cache: dict[int, Mapping[str, Any] | None] | None,
) -> Mapping[str, Any] | None:
    """Load one role detail once for the lifetime of a rendered result."""

    cache = detail_cache if detail_cache is not None else {}
    if character_id not in cache:
        try:
            cache[character_id] = load_official_role_detail(
                runtime.USER_DATABASE_PATH,
                character_id,
            )
        except (OSError, ValueError):
            cache[character_id] = None
    return cache[character_id]


def _result_badge(title: str, value: str, color: str) -> QWidget:
    frame = QFrame()
    frame.setStyleSheet(
        f"QFrame{{background:{theme_rgba(color, 0.10)};border:1px solid {color};"
        "border-radius:7px;padding:4px 12px}"
    )
    layout = QHBoxLayout(frame)
    layout.setSpacing(6)
    layout.setContentsMargins(4, 0, 4, 0)
    layout.addWidget(QLabel(title))
    value_label = QLabel(value)
    value_label.setStyleSheet(f"font-size:14px;font-weight:800;color:{color};border:none")
    layout.addWidget(value_label)
    return frame


def _display_weights(window, role) -> dict[str, float]:
    labels = getattr(window, "_weighted_property_names", {})
    return {
        labels.get(property_id, property_id): float(weight)
        for property_id, weight in (getattr(role, "effective_property_weights", ()) if role else ())
    }


def _display_main_weights(window, role) -> dict[str, float]:
    labels = getattr(window, "_weighted_property_names", {})
    return {
        labels.get(property_id, property_id): float(weight)
        for property_id, weight in (
            getattr(role, "effective_main_property_weights", ()) if role else ()
        )
    }


def _geometry_key(value: str | None) -> str:
    return str(value or "").strip().removeprefix("EquipmentGeometry_").casefold()


def _shape_resource_ids(context: AllocationContext | None) -> dict[str, str]:
    return {
        _geometry_key(shape.shape_id): str(shape.legacy_shape_id)
        for shape in (context.shapes if context else ())
        if shape.legacy_shape_id
    }


def _shape_resource_id(geometry: str | None, shape_resources: dict[str, str]) -> str:
    value = str(geometry or "").strip()
    mapped = shape_resources.get(_geometry_key(value))
    if mapped:
        return mapped
    if value in PuzzleBoardWidget.SHAPE_HUE:
        return value
    try:
        return legacy_shape_id(value)
    except AllocationInventoryProjectionError:
        return value


def _geometry_display_name(geometry: str | None) -> str:
    return str(geometry or "").strip().removeprefix("EquipmentGeometry_").upper()


def _legacy_equipment_source(
    window, assignment, candidates: dict, shape_resources: dict[str, str],
) -> dict[str, Any]:
    candidate = candidates.get(assignment.uid)
    labels = getattr(window, "_weighted_property_names", {})
    item_names = getattr(window, "_weighted_item_names", {})
    sub_stats = {
        labels.get(stat.property_id, stat.property_id): _display_stat_value(stat.value, stat.percent)
        for stat in (candidate.sub_stats if candidate else ())
    }
    main_stats = {
        labels.get(stat.property_id, stat.property_id): _display_stat_value(stat.value, stat.percent)
        for stat in (candidate.main_stats if candidate else ())
    }
    is_core = assignment.kind == "core"
    # 旧版驱动卡只展示副词条；驱动快照中的 main_stats 不能被投影成顶部蓝色主词条。
    main_stat = next(iter(main_stats), "") if is_core else ""
    shape = None if is_core else _shape_resource_id(assignment.geometry, shape_resources)
    area = 15 if assignment.kind == "core" else int(candidate.grid_count or 0) if candidate else 0
    return {
        "type": "tape" if is_core else "drive",
        "uid": f"nte-{'core' if is_core else 'module'}-{assignment.uid[0]}-{assignment.uid[1]}",
        "display_name": item_names.get(assignment.item_id, assignment.item_id),
        "set_name": item_names.get(assignment.item_id, assignment.item_id),
        "main_stats": main_stat,
        "main_value": main_stats.get(main_stat) if is_core else None,
        "sub_stats": sub_stats,
        "shape_id": shape,
        "area": area,
        "quality": _legacy_quality(candidate.quality if candidate else None),
        "icon_path": getattr(window, "_weighted_item_icons", {}).get(assignment.item_id),
    }


def _display_stat_value(value: float, percent: bool) -> float:
    """Hide binary float tails without changing the value used by the solver."""

    return round(float(value) * (100.0 if percent else 1.0), 2)


def _allocation_candidate_row(window, assignment, candidate) -> dict[str, Any]:
    labels = getattr(window, "_weighted_property_names", {})
    item_names = getattr(window, "_weighted_item_names", {})
    suit_names = getattr(window, "_weighted_suit_names", {})

    def stats(values) -> list[dict[str, Any]]:
        return [
            {
                "property_id": stat.property_id,
                "value": float(stat.value),
                "percent": bool(stat.percent),
                "names": {
                    "zh_cn": labels.get(stat.property_id, stat.property_id),
                },
            }
            for stat in values
        ]

    if candidate is None:
        if getattr(assignment, "virtual", False):
            item = virtual_equipment_inventory_item({
                "uid_slot": assignment.uid[0],
                "uid_serial": assignment.uid[1],
                "kind": assignment.kind,
                "geometry": assignment.geometry,
                "grid_count": assignment.grid_count,
                "virtual": True,
                "virtual_equipment": {
                    "item_id": assignment.item_id,
                    "kind": assignment.kind,
                    "suit_id": assignment.suit_id,
                    "geometry": assignment.geometry,
                    "grid_count": assignment.grid_count,
                    "quality": "orange",
                },
            })
            item["names"] = {
                "zh_cn": item_names.get(
                    assignment.item_id, assignment.item_id
                )
            }
            item["suit_names"] = {
                "zh_cn": suit_names.get(
                    assignment.suit_id,
                    assignment.suit_id or "",
                )
            }
            return item
        return {
            "uid": {"slot": assignment.uid[0], "serial": assignment.uid[1]},
            "uid_slot": assignment.uid[0],
            "uid_serial": assignment.uid[1],
            "kind": assignment.kind,
            "item_id": assignment.item_id,
            "suit_id": assignment.suit_id,
            "geometry": assignment.geometry,
            "grid_count": assignment.grid_count,
            "quality": "orange",
            "level": 0,
            "max_level": 0,
            "names": {
                "zh_cn": item_names.get(assignment.item_id, assignment.item_id),
            },
            "suit_names": {
                "zh_cn": suit_names.get(
                    assignment.suit_id,
                    assignment.suit_id or "",
                ),
            },
            "main_stats": (),
            "sub_stats": (),
        }
    return {
        "uid": {"slot": candidate.uid_slot, "serial": candidate.uid_serial},
        "uid_slot": candidate.uid_slot,
        "uid_serial": candidate.uid_serial,
        "kind": candidate.kind,
        "item_id": candidate.item_id,
        "suit_id": candidate.suit_id,
        "geometry": candidate.geometry,
        "grid_count": candidate.grid_count,
        "quality": candidate.quality,
        "level": candidate.level,
        "max_level": candidate.max_level,
        "names": {
            "zh_cn": item_names.get(candidate.item_id, candidate.item_id),
        },
        "suit_names": {
            "zh_cn": suit_names.get(candidate.suit_id, candidate.suit_id or ""),
        },
        "main_stats": stats(candidate.main_stats),
        "sub_stats": stats(candidate.sub_stats),
    }


def _allocation_direct_damage_scores(
    window,
    option: RoleAllocationOption,
    candidates: Mapping[tuple[int, int], Any],
    *,
    detail: Mapping[str, Any] | None = None,
) -> dict[tuple[int, int], float]:
    items_by_uid = {
        assignment.uid: _allocation_candidate_row(
            window,
            assignment,
            candidates.get(assignment.uid),
        )
        for assignment in option.assignments
    }
    if not items_by_uid:
        return {}
    if detail is None:
        return {}
    with StaticGameDataDao() as static_dao:
        calculation_items = project_equipment_items_to_max_level(
            items_by_uid.values(), static_dao,
        )
    context_key = "_weighted_result"
    detail = {
        **detail,
        "equipment_contexts": {
            **(detail.get("equipment_contexts") or {}),
            context_key: {
                "title": "词条配装结果",
                "items": tuple(items_by_uid.values()),
                "calculation_items": tuple(calculation_items),
                "available": True,
            },
        },
    }
    result: dict[tuple[int, int], float] = {}
    for uid, item in items_by_uid.items():
        gain = calculate_official_role_item_gain(detail, context_key, item)
        if gain is not None:
            result[uid] = float(gain["gain_percent"])
    return result


def _official_summary_rows_by_mode(
    window,
    loadout: AttributeSummaryLoadout,
    role=None,
    detail: Mapping[str, Any] | None = None,
) -> dict[str, tuple[AttributeSummaryRow, ...]]:
    selected = [
        item
        for item in (loadout.core, *loadout.drives)
        if item is not None
    ]
    if detail is None:
        return {"equipment": (), "character": ()}
    summaries = calculate_official_role_attribute_summaries(
        detail,
        selected,
    )
    weights = dict(
        getattr(role, "effective_property_weights", ()) if role else ()
    )

    def rows(mode: str) -> tuple[AttributeSummaryRow, ...]:
        result = [
            AttributeSummaryRow(
                key=total.key,
                label=total.label,
                value=_display_stat_value(total.value, total.percent),
                percent=total.percent,
                weight=max(
                    (
                        float(weights.get(property_id, 0.0))
                        for property_id in total.weight_property_ids
                    ),
                    default=0.0,
                ),
            )
            for total in summaries.get(mode, ())
        ]
        result.sort(key=lambda item: (-item.weight, item.label))
        return tuple(result)

    return {
        "equipment": rows("equipment"),
        "character": rows("character"),
    }


def _official_bonus_summary_panel(
    window,
    role_name: str,
    character_id: int,
    core,
    drives,
    role,
    detail: Mapping[str, Any] | None = None,
) -> QWidget:
    if detail is None:
        return None
    return AttributeSummaryPanel.from_loadout(
        role_name,
        character_id=character_id,
        core=core,
        drives=drives,
        selected_core_type=(
            getattr(role, "core_main_property_id", None)
            if role is not None
            else None
        ),
        rows_provider=lambda loadout: _official_summary_rows_by_mode(
            window,
            loadout,
            role,
            detail,
        ),
        parent=window if isinstance(window, QWidget) else None,
        color_for_weight=lambda weight: legacy_results._stat_c(window, weight),
    )


def _result_equipment_card(
    window, assignment, candidates: dict, weights: dict, main_weights: dict,
    shape_resources: dict[str, str],
    replacement_callback=None,
    direct_damage_score: float | None = None,
) -> QWidget:
    del shape_resources
    candidate = candidates.get(assignment.uid)
    item = _allocation_candidate_row(window, assignment, candidate)
    view = warehouse_item_view(item)
    icon_path = getattr(window, "_weighted_item_icons", {}).get(
        assignment.item_id
    )
    if icon_path:
        view["item_icon_path"] = icon_path
    area = (
        15
        if assignment.kind == "core"
        else int(candidate.grid_count or 0)
        if candidate is not None
        else int(assignment.grid_count or 0)
    )
    card = WarehouseResultCard(
        view,
        score=assignment.score,
        grade=legacy_results._calc_grade(window, assignment.score, area),
        direct_damage_score=direct_damage_score,
        replacement_callback=replacement_callback,
        parent=window if isinstance(window, QWidget) else None,
    )
    tooltip = _assignment_weight_tooltip(
        window, assignment, candidate, weights, main_weights,
    )
    if tooltip:
        card.setToolTip("\n".join(filter(None, (card.toolTip(), tooltip))))
    return card


def _assignment_weight_tooltip(
    window, assignment, candidate, weights: Mapping[str, float],
    main_weights: Mapping[str, float],
) -> str:
    """Expose the exact account SQLite weights used by a result card."""

    if candidate is None:
        return ""
    labels = getattr(window, "_weighted_property_names", {})
    lines = ["账号 SQLite 词条权重"]
    if assignment.kind == "core":
        for stat in candidate.main_stats:
            property_id = str(stat.property_id)
            lines.append(
                f"主词条 {labels.get(property_id, property_id)}："
                f"{float(main_weights.get(property_id, 0.0)):g}"
            )
    for stat in candidate.sub_stats:
        property_id = str(stat.property_id)
        lines.append(
            f"副词条 {labels.get(property_id, property_id)}："
            f"{float(weights.get(property_id, 0.0)):g}"
        )
    return "\n".join(lines) if len(lines) > 1 else ""


def _legacy_quality(quality: str | None) -> str:
    """Translate the v5 inventory spelling to the established result-card API."""

    return {"orange": "Gold", "purple": "Purple", "blue": "Blue"}.get(
        str(quality or "").lower(), "Gold"
    )


def _unassigned_reason(window, context: AllocationContext | None, ids: tuple[int, ...]) -> str:
    if context is None:
        return "部分角色没有可用的完整方案。"
    names = getattr(window, "_weighted_role_names", {})
    suits = getattr(window, "_weighted_suit_names", {})
    attributes = getattr(window, "_weighted_property_names", {})
    reasons = []
    for role in context.roles:
        if role.character_id not in ids:
            continue
        cores = [item for item in context.candidates if item.kind == "core"]
        if role.target_suit_id:
            cores = [item for item in cores if item.suit_id == role.target_suit_id]
        if role.core_main_property_id:
            cores = [item for item in cores if any(stat.property_id == role.core_main_property_id for stat in item.main_stats)]
        if not cores:
            suit = suits.get(role.target_suit_id, role.target_suit_id or "任意套装")
            attribute = attributes.get(role.core_main_property_id, role.core_main_property_id or "任意主词条")
            reasons.append(f"{names.get(role.character_id, role.character_id)}：缺少 {suit}＋{attribute} 主词条空幕")
        else:
            reasons.append(f"{names.get(role.character_id, role.character_id)}：缺少可组成完整图纸的驱动")
    return "；".join(reasons)


def _missing_core_text(window, role) -> str:
    if role is None:
        return "空幕未分配"
    suits = getattr(window, "_weighted_suit_names", {})
    attributes = getattr(window, "_weighted_property_names", {})
    suit = suits.get(role.target_suit_id, role.target_suit_id or "任意套装")
    attribute = attributes.get(role.core_main_property_id, role.core_main_property_id or "任意主词条")
    return f"空幕缺失：缺少 {suit}＋{attribute} 主词条空幕（驱动图纸已匹配）"
