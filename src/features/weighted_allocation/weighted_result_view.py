# 提供只含角色优先级、计算和统一结果的词条配装页面。
"""Minimal role-priority UI for the audited weighted-allocation facade."""

from __future__ import annotations

from typing import Any, Mapping

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QGridLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import GRADE_COLORS, theme_color, theme_rgba, themed_style
from src.domain.allocation_rating import allocation_grade
from src.features.weighted_allocation.result_equipment_card import (
    result_equipment_card,
)
from src.features.weighted_allocation.result_messages import (
    missing_core_text as _missing_core_text,
    unassigned_reason as _unassigned_reason,
)
from src.features.weighted_allocation.result_styles import (
    clear_layout as _clear_layout,
    section_label as _section_label,
    weight_color as _weight_color,
)
from src.features.weighted_allocation.runner import (
    WeightedAllocationPreview,
)
from src.services.allocation_solver import RoleAllocationOption
from src.services.allocation_context import AllocationContext
from src.services.equipment_level_projection_service import (
    project_equipment_items_to_max_level,
)
from src.services.official_role_page_service import (
    calculate_official_role_attribute_summaries,
    calculate_official_role_item_gain,
)
from src.services.virtual_equipment_service import (
    virtual_equipment_inventory_item,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.ui.attribute_summary_panel import (
    AttributeSummaryLoadout,
    AttributeSummaryPanel,
    AttributeSummaryRow,
)
from src.ui.puzzle_board import PuzzleBoardWidget


_INTERNAL_PROFILE_NAME = "__weighted_allocation_role_priority__"
# 普通入口不展示候选；避免为不可见的 Top-K 重复执行昂贵的 DFS 与评分。
_INTERNAL_TOP_K = 1

_MAIN_PROPERTY_CHOICES = (
    ("生命值百分比", "HPMaxUp"),
    ("攻击力百分比", "AtkUp"),
    ("防御力百分比", "DefUp"),
    ("暴击率", "CritBase"),
    ("暴击伤害", "CritDamageBase"),
    ("环合强度", "MagBase"),
    ("倾陷强度", "UnbalIntensityBase"),
    ("治疗加成", "HealUp"),
    ("光属性异能伤害增强", "DamageUpCosmosBase"),
    ("灵属性异能伤害增强", "DamageUpNatureBase"),
    ("咒属性异能伤害增强", "DamageUpIncantationBase"),
    ("暗属性异能伤害增强", "DamageUpChaosBase"),
    ("魂属性异能伤害增强", "DamageUpPsycheBase"),
    ("相属性异能伤害增强", "DamageUpLakshanaBase"),
    ("心灵伤害增强", "DamageUpPsychicallyBase"),
)
_SUBSTAT_PROPERTY_CHOICES = (
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
_RESULT_PROPERTY_LABELS = {property_id: label for label, property_id in _SUBSTAT_PROPERTY_CHOICES}
_RESULT_PROPERTY_LABELS.update(
    {
        property_id: f"{label}%" if "伤害增强" in label or "治疗加成" in label else label
        for label, property_id in _MAIN_PROPERTY_CHOICES
        if property_id not in _RESULT_PROPERTY_LABELS
    }
)


def _request_weighted_equipment(*args, **kwargs):
    from .weighted_workflow import _request_weighted_equipment as request

    return request(*args, **kwargs)


def _request_weighted_replacement(*args, **kwargs):
    from .weighted_workflow import _request_weighted_replacement as request

    return request(*args, **kwargs)


def render_weighted_allocation_result(
    window,
    preview: WeightedAllocationPreview,
    *,
    restore_scroll_value: int | None = None,
    restore_character_id: int | None = None,
    restore_viewport_offset: int | None = None,
) -> None:
    """Render one frozen preview without reopening mutable account data."""

    result = preview.result
    context = preview.context
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
    detail_cache: dict[int, Mapping[str, Any] | None] = dict(preview.role_details)
    card_layout.addWidget(
        _LazyWeightedRoleCards(
            window,
            tuple(result.unified.selected),
            candidates,
            role_preferences,
            shape_resources,
            detail_cache,
            restore_scroll_value=restore_scroll_value,
            restore_character_id=restore_character_id,
            restore_viewport_offset=restore_viewport_offset,
            parent=card,
        )
    )
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
        restore_scroll_value: int | None = None,
        restore_character_id: int | None = None,
        restore_viewport_offset: int | None = None,
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
        self._restore_scroll_value = restore_scroll_value
        self._restore_character_id = restore_character_id
        self._restore_viewport_offset = restore_viewport_offset
        self._loaded_cards: dict[int, QWidget] = {}
        self._restore_attempts = 0
        self._restore_scheduled = False
        for option in options:
            placeholder = QFrame(self)
            placeholder.setProperty("weighted_character_id", option.character_id)
            placeholder.setMinimumHeight(self._PLACEHOLDER_HEIGHT)
            placeholder.setStyleSheet(
                themed_style("QFrame{border:1px solid #30363d;border-radius:10px;background:#0d1117}")
            )
            placeholder_layout = QVBoxLayout(placeholder)
            name = getattr(window, "_weighted_role_names", {}).get(option.character_id, "角色")
            placeholder_layout.addWidget(QLabel(f"正在准备 {name} 的结果…"))
            placeholder_layout.addStretch()
            self._layout.addWidget(placeholder)
            self._pending[placeholder] = option
        page_scroll = getattr(window, "weighted_page_scroll", None)
        self._page_scroll = page_scroll if isinstance(page_scroll, QScrollArea) else None
        if self._page_scroll is not None:
            scrollbar = self._page_scroll.verticalScrollBar()
            scrollbar.valueChanged.connect(self._load_visible_cards)
            # ``setWidgetResizable`` recalculates this range after each lazy
            # card replaces its placeholder.  Restore only after the current
            # range exists instead of letting an early setValue clamp to zero.
            scrollbar.rangeChanged.connect(self._schedule_scroll_restore)
        QTimer.singleShot(0, self._load_visible_cards)
        self._schedule_scroll_restore()

    def _load_visible_cards(self, *_args) -> None:
        if not self._pending:
            return
        if self._page_scroll is None:
            targets = tuple(self._pending)
        else:
            viewport = self._page_scroll.viewport()
            targets = tuple(
                placeholder for placeholder in self._pending if self._is_near_viewport(placeholder, viewport)
            )
            # Before the layout has been shown Qt can report no geometry.  The
            # first card must still appear so the user gets an immediate result.
            if not targets and self._initial_load:
                targets = (next(iter(self._pending)),)
            if self._restore_character_id is not None:
                focus = next(
                    (
                        placeholder
                        for placeholder, option in self._pending.items()
                        if option.character_id == self._restore_character_id
                    ),
                    None,
                )
                if focus is not None and focus not in targets:
                    targets = (*targets, focus)
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
            role_card = _role_option_card(
                self._window,
                option,
                dict(self._candidates),
                self._roles.get(option.character_id),
                dict(self._shape_resources),
                self._detail_cache,
            )
            role_card.setProperty("weighted_character_id", option.character_id)
            self._loaded_cards[option.character_id] = role_card
            self._layout.insertWidget(index, role_card)
        if self._pending:
            QTimer.singleShot(0, self._load_visible_cards)
        self._schedule_scroll_restore()

    def _schedule_scroll_restore(self, *_args) -> None:
        if self._page_scroll is None:
            return
        if self._restore_scheduled:
            return
        focus_card = self._loaded_cards.get(self._restore_character_id)
        if focus_card is None and self._restore_scroll_value is None:
            return
        self._restore_scheduled = True
        QTimer.singleShot(50, self._restore_scroll_position)

    def _restore_scroll_position(self) -> None:
        self._restore_scheduled = False
        if self._page_scroll is None:
            return
        focus_card = self._loaded_cards.get(self._restore_character_id)
        if focus_card is None and self._restore_scroll_value is None:
            return

        page = self._page_scroll.widget()
        if page is not None and page.layout() is not None:
            page.layout().activate()
        self._layout.activate()
        scrollbar = self._page_scroll.verticalScrollBar()
        if focus_card is not None and self._restore_viewport_offset is not None:
            if page is not None:
                target_top = focus_card.mapTo(page, QPoint(0, 0)).y()
                desired_value = max(0, target_top - self._restore_viewport_offset)
                scrollbar.setValue(desired_value)
                viewport = self._page_scroll.viewport()
                target_top_in_view = viewport.mapFromGlobal(focus_card.mapToGlobal(QPoint(0, 0))).y()
                if target_top_in_view < 0 or target_top_in_view > viewport.height() - 24:
                    self._page_scroll.ensureWidgetVisible(focus_card, 0, 24)
        elif self._restore_scroll_value is not None:
            scrollbar.setValue(self._restore_scroll_value)

        self._restore_attempts += 1
        if self._restore_attempts < 6:
            # The final loaded role card is taller than its placeholder.  A few
            # short retries preserve the position through those size changes.
            self._schedule_scroll_restore()
        else:
            self._restore_scroll_value = None
            self._restore_character_id = None
            self._restore_viewport_offset = None

    def _is_near_viewport(self, placeholder: QWidget, viewport: QWidget) -> bool:
        top = viewport.mapFromGlobal(placeholder.mapToGlobal(QPoint(0, 0))).y()
        bottom = top + max(placeholder.height(), self._PLACEHOLDER_HEIGHT)
        return bottom >= -self._PREFETCH_PIXELS and top <= viewport.height() + self._PREFETCH_PIXELS


def _role_option_card(
    window,
    option: RoleAllocationOption,
    candidates: dict = None,
    role=None,
    shape_resources: dict[str, str] | None = None,
    detail_cache: dict[int, Mapping[str, Any] | None] | None = None,
) -> QWidget:
    name = getattr(window, "_weighted_role_names", {}).get(option.character_id, "角色")
    card = QGroupBox()
    card.setStyleSheet(
        themed_style(
            "QGroupBox{background:#0d1117;border:1px solid #30363d;border-radius:10px;margin-top:12px;padding:18px}"
        )
    )
    layout = QVBoxLayout(card)
    layout.setSpacing(10)
    core = next((item for item in option.assignments if item.kind == "core"), None)
    modules = [item for item in option.assignments if item.kind == "module"]
    grade = allocation_grade(option.score, 35)
    grade_color = GRADE_COLORS.get(grade, "#58a6ff")
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
    equip_button.setEnabled(
        core is not None
        and bool(getattr(window, "_weighted_equipment_actions_available", False))
    )
    equip_button.clicked.connect(
        lambda _checked=False, current_name=name: _request_weighted_equipment(
            window,
            mode="configured",
            role_name=current_name,
        )
    )
    window._weighted_role_equip_buttons.append(equip_button)
    role_header.addWidget(equip_button)
    layout.addLayout(role_header)
    layout.addSpacing(6)

    candidate_map = candidates or {}
    summary_core = candidate_map.get(core.uid) if core is not None else None
    summary_drives = [
        candidate for assignment in modules if (candidate := candidate_map.get(assignment.uid)) is not None
    ]
    detail = _weighted_result_role_detail(
        option.character_id,
        detail_cache,
    )
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
        layout.addWidget(_section_label("拼图图纸:"))
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
    main_weights = dict(getattr(role, "effective_main_property_weights", ()) if role else ())
    if core is None:
        missing_core = QLabel(
            _missing_core_text(window, role, option.missing_core_reason)
        )
        missing_core.setWordWrap(True)
        layout.addWidget(missing_core)
    equipment_assignments = ([core] if core is not None else []) + modules
    if equipment_assignments:
        direct_damage_scores = _allocation_direct_damage_scores(
            window,
            option,
            candidate_map,
            detail=detail,
        )
        layout.addWidget(_section_label(f"空幕 / 驱动 ({len(equipment_assignments)}件):"))
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
                        window,
                        name,
                        current,
                        role,
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
    character_id: int,
    detail_cache: dict[int, Mapping[str, Any] | None] | None,
) -> Mapping[str, Any] | None:
    """Read role detail captured with the immutable calculation preview."""

    cache = detail_cache if detail_cache is not None else {}
    return cache.get(character_id)


def _result_badge(title: str, value: str, color: str) -> QWidget:
    frame = QFrame()
    frame.setStyleSheet(
        f"QFrame{{background:{theme_rgba(color, 0.10)};border:1px solid {color};border-radius:7px;padding:4px 12px}}"
    )
    layout = QHBoxLayout(frame)
    layout.setSpacing(6)
    layout.setContentsMargins(4, 0, 4, 0)
    layout.addWidget(QLabel(title))
    value_label = QLabel(value)
    value_label.setStyleSheet(f"font-size:14px;font-weight:800;color:{color};border:none")
    layout.addWidget(value_label)
    return frame


def _geometry_key(value: str | None) -> str:
    return str(value or "").strip().removeprefix("EquipmentGeometry_").casefold()


def _shape_resource_ids(context: AllocationContext | None) -> dict[str, str]:
    return {
        _geometry_key(shape.shape_id): str(shape.legacy_shape_id)
        for shape in (context.shapes if context else ())
        if shape.legacy_shape_id
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
            item = virtual_equipment_inventory_item(
                {
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
                }
            )
            item["names"] = {"zh_cn": item_names.get(assignment.item_id, assignment.item_id)}
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
            items_by_uid.values(),
            static_dao,
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
    selected = [item for item in (loadout.core, *loadout.drives) if item is not None]
    if detail is None:
        return {"equipment": (), "character": ()}
    summaries = calculate_official_role_attribute_summaries(
        detail,
        selected,
    )
    weights = dict(getattr(role, "effective_property_weights", ()) if role else ())

    def rows(mode: str) -> tuple[AttributeSummaryRow, ...]:
        result = [
            AttributeSummaryRow(
                key=total.key,
                label=total.label,
                value=_display_stat_value(total.value, total.percent),
                percent=total.percent,
                weight=max(
                    (float(weights.get(property_id, 0.0)) for property_id in total.weight_property_ids),
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
        selected_core_type=(getattr(role, "core_main_property_id", None) if role is not None else None),
        rows_provider=lambda loadout: _official_summary_rows_by_mode(
            window,
            loadout,
            role,
            detail,
        ),
        parent=window if isinstance(window, QWidget) else None,
        color_for_weight=_weight_color,
    )


def _result_equipment_card(
    window,
    assignment,
    candidates: dict,
    weights: dict,
    main_weights: dict,
    shape_resources: dict[str, str],
    replacement_callback=None,
    direct_damage_score: float | None = None,
) -> QWidget:
    del shape_resources
    return result_equipment_card(
        window,
        assignment,
        candidates,
        weights,
        main_weights,
        candidate_row_builder=_allocation_candidate_row,
        replacement_callback=replacement_callback,
        direct_damage_score=direct_damage_score,
    )
