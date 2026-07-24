# 提供只含角色优先级、计算和统一结果的词条配装页面。
"""Minimal role-priority UI for the audited weighted-allocation facade."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from PySide6.QtCore import Qt
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
from .weighted_preferences import (
    _load_weighted_persistence,
    _mark_weighted_preferences_dirty,
    _show_empty_curtain_preferences,
)
from .weighted_workflow import (
    _request_weighted_equipment,
    start_weighted_allocation,
    start_weighted_allocation_save,
)


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


def build_weighted_allocation_page(window) -> QWidget:
    page = QWidget()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(12)
    title = QLabel("词条配装")
    title.setObjectName("cardTitle")
    layout.addWidget(title)
    layout.addWidget(QLabel("选择角色并调整优先级，然后开始统一配装。"))

    selector_card = window._card("角色优先级")
    window.weighted_role_selector = RoleSelector(
        parent=selector_card,
        priority_config_path_provider=lambda: Path("__weighted_ui_unused__"),
        preference_dialog_callback=lambda name: _show_empty_curtain_preferences(window, name),
    )
    selector_card.layout().addWidget(window.weighted_role_selector)
    window.weighted_role_selector.orderChanged.connect(
        lambda: (
            _hide_legacy_selector_controls(window.weighted_role_selector),
            _mark_weighted_preferences_dirty(window),
        )
    )
    layout.addWidget(selector_card)

    actions = QHBoxLayout()
    window.weighted_run_button = QPushButton("开始计算")
    window.weighted_run_button.setObjectName("btnPrimary")
    window.weighted_save_button = QPushButton("保存方案")
    window.weighted_save_button.setEnabled(False)
    window.weighted_one_key_button = QPushButton("一键装配")
    window.weighted_one_key_button.setObjectName("btnPrimary")
    window.weighted_one_key_button.setEnabled(False)
    window.weighted_one_key_button.setToolTip("按设置中的装配执行方式装配当前统一方案")
    window.weighted_automatic_button = QPushButton("自动装配")
    window.weighted_automatic_button.setObjectName("btnPrimary")
    window.weighted_automatic_button.setEnabled(False)
    window.weighted_automatic_button.setToolTip("模拟游戏内操作，逐步装配当前统一方案")
    window._weighted_role_equip_buttons = []
    window.weighted_run_button.clicked.connect(lambda: start_weighted_allocation(window))
    window.weighted_save_button.clicked.connect(lambda: start_weighted_allocation_save(window))
    window.weighted_one_key_button.clicked.connect(
        lambda: _request_weighted_equipment(window, mode="configured")
    )
    window.weighted_automatic_button.clicked.connect(
        lambda: _request_weighted_equipment(window, mode="automatic")
    )
    actions.addStretch()
    actions.addWidget(window.weighted_save_button)
    actions.addWidget(window.weighted_one_key_button)
    actions.addWidget(window.weighted_automatic_button)
    actions.addWidget(window.weighted_run_button)
    layout.addLayout(actions)
    window.weighted_status_label = QLabel("")
    window.weighted_status_label.setWordWrap(True)
    layout.addWidget(window.weighted_status_label)
    window.weighted_result_widget = QWidget()
    window.weighted_result_layout = QVBoxLayout(window.weighted_result_widget)
    window.weighted_result_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(window.weighted_result_widget)
    layout.addStretch()
    refresh_weighted_allocation_page(window)
    return scroll


def _hide_legacy_selector_controls(selector: RoleSelector) -> None:
    for button in selector.findChildren(QPushButton):
        if button.text() in {"恢复", "保存", "读取", "重置"}:
            button.hide()


def refresh_weighted_allocation_page(window) -> None:
    if not hasattr(window, "weighted_role_selector"):
        return
    try:
        with StaticGameDataDao() as dao:
            all_characters = [row for row in dao.list_characters() if dao.get_equipment_plan(int(row["character_id"]))]
            characters = all_characters
            suit_defaults: dict[int, str] = {}
            for row in characters:
                character_id = int(row["character_id"])
                plan = dao.get_equipment_plan(character_id) or {}
                core_template = dao.get_equipment_item(str(plan.get("core_item_id") or ""))
                if core_template and core_template.get("suit_id"):
                    suit_defaults[character_id] = str(core_template["suit_id"])
            attributes = dao.list_equipment_attributes()
            known_attribute_ids = {str(row["attribute_id"]) for row in attributes}
            main_choices = [choice for choice in _MAIN_PROPERTY_CHOICES if choice[1] in known_attribute_ids]
            substat_choices = [choice for choice in _SUBSTAT_PROPERTY_CHOICES if choice[1] in known_attribute_ids]
            window._weighted_suit_names = {
                str(row["suit_id"]): str(row.get("name_zh") or row["suit_id"])
                for row in dao.list_suits()
            }
            window._weighted_property_names = {
                str(row["attribute_id"]): _RESULT_PROPERTY_LABELS.get(
                    str(row["attribute_id"]),
                    str(row.get("filter_name_zh") or row.get("display_name_zh") or row["attribute_id"]),
                )
                for row in attributes
            }
            window._weighted_property_percent = {
                str(row["attribute_id"]): bool(row.get("show_percent"))
                for row in attributes
            }
            window._weighted_main_property_by_label = dict(main_choices)
            window._weighted_substat_property_by_label = dict(substat_choices)
            equipment_items = dao.list_equipment_items()
            window._weighted_item_names = {
                str(row["item_id"]): str(row.get("name_zh") or row["item_id"])
                for row in equipment_items
            }
            asset_catalog = GameUiAssetCatalog(runtime.ASSET_DIR / "game_ui")
            window._weighted_item_icons = {
                str(row["item_id"]): asset_catalog.inventory_item_icon(
                    str(row.get("kind") or ""),
                    str(row["item_id"]),
                )
                for row in equipment_items
            }
        role_names = {str(row.get("name_zh") or row["character_id"]): int(row["character_id"]) for row in characters}
        window._weighted_role_ids = role_names
        window._weighted_role_names = {value: key for key, value in role_names.items()}
        window._weighted_default_suits = suit_defaults
        database_path = Path(runtime.USER_DATABASE_PATH)
        account_weights = ensure_account_character_weights(
            database_path, role_names.values(),
        )
        window._weighted_default_property_weights = {
            character_id: dict(row.get("property_weights") or {})
            for character_id, row in account_weights.items()
        }
        database_changed = getattr(window, "_weighted_persistence_database_path", None) != database_path
        window._weighted_preference_overrides = getattr(window, "_weighted_preference_overrides", {})
        suit_names = window._weighted_suit_names
        window.weighted_role_selector.load_roles(
            {
                name: {"default_set": suit_names.get(suit_defaults.get(character_id), "")}
                for name, character_id in role_names.items()
            },
            list(suit_names.values()),
            list(window._weighted_main_property_by_label),
            list(window._weighted_substat_property_by_label),
            weapons_db={},
        )
        _hide_legacy_selector_controls(window.weighted_role_selector)
        if database_changed:
            _load_weighted_persistence(window, database_path)
        elif not window.weighted_status_label.text():
            window.weighted_status_label.setText("请选择角色并设置优先级。")
    except Exception as exc:
        window.weighted_status_label.setText(f"无法读取角色目录：{exc}")


