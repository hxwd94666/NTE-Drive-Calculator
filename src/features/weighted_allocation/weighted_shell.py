# 提供只含角色优先级、计算和统一结果的词条配装页面。
"""Minimal role-priority UI for the audited weighted-allocation facade."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.i18n import tr
from src.features.allocation.role_selector import RoleSelector
from src.services.character_weight_service import (
    ensure_account_character_weights,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from .weighted_static_catalog import get_weighted_static_catalog
from .dependencies import weighted_allocation_dependencies
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
    # Result cards subscribe to this viewport and are created only when they
    # enter it.  Retaining the reference also keeps the old page API intact.
    window.weighted_page_scroll = scroll
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(12)
    title = QLabel(tr("词条配装"))
    title.setObjectName("cardTitle")
    layout.addWidget(title)
    layout.addWidget(QLabel(tr("选择角色并调整优先级，然后开始统一配装。")))

    selector_card = window._card(tr("角色优先级"))
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
    window.weighted_run_button = QPushButton(tr("开始计算"))
    window.weighted_run_button.setObjectName("btnPrimary")
    window.weighted_save_button = QPushButton(tr("保存方案"))
    window.weighted_save_button.setEnabled(False)
    window.weighted_one_key_button = QPushButton(tr("一键装配"))
    window.weighted_one_key_button.setObjectName("btnPrimary")
    window.weighted_one_key_button.setEnabled(False)
    window.weighted_one_key_button.setToolTip(tr("按设置中的装配执行方式装配当前统一方案"))
    window.weighted_automatic_button = QPushButton(tr("自动装配"))
    window.weighted_automatic_button.setObjectName("btnPrimary")
    window.weighted_automatic_button.setEnabled(False)
    window.weighted_automatic_button.setToolTip(tr("模拟游戏内操作，逐步装配当前统一方案"))
    window._weighted_role_equip_buttons = []
    window.weighted_run_button.clicked.connect(lambda: start_weighted_allocation(window))
    window.weighted_save_button.clicked.connect(lambda: start_weighted_allocation_save(window))
    window.weighted_one_key_button.clicked.connect(lambda: _request_weighted_equipment(window, mode="configured"))
    window.weighted_automatic_button.clicked.connect(lambda: _request_weighted_equipment(window, mode="automatic"))
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
        elif button.text() == "?":
            button.hide()
    for label in selector.findChildren(QLabel):
        if "重置只影响当前界面" in label.text():
            label.setText(
                tr("点击选择角色并调整顺序；“=”表示同级联合分配。"
                "角色管理设置会在计算时自动保存到当前账号。")
            )


def refresh_weighted_allocation_page(window) -> None:
    if not hasattr(window, "weighted_role_selector"):
        return
    try:
        dependencies = weighted_allocation_dependencies(window)
        catalog = get_weighted_static_catalog(dependencies.game_ui_asset_root)
        characters = [row for row in catalog.characters if int(row["character_id"]) in catalog.plans_by_character_id]
        database_path = dependencies.user_database_path
        with UserDataDao(database_path) as user_dao:
            custom_characters = user_dao.list_custom_characters()
            custom_weights = {
                int(row["character_id"]): (
                    user_dao.get_character_weight_preferences(int(row["character_id"])) or {}
                )
                for row in custom_characters
            }
        characters.extend({**row, "is_custom": True} for row in custom_characters)
        suit_defaults: dict[int, str] = {}
        item_by_id = {str(row["item_id"]): row for row in catalog.equipment_items}
        for row in characters:
            character_id = int(row["character_id"])
            if row.get("is_custom"):
                target_suit_id = str(row.get("target_suit_id") or "")
                if target_suit_id:
                    suit_defaults[character_id] = target_suit_id
                continue
            plan = catalog.plans_by_character_id.get(character_id, {})
            core_template = item_by_id.get(str(plan.get("core_item_id") or "")) or {}
            if core_template.get("suit_id"):
                suit_defaults[character_id] = str(core_template["suit_id"])
        attributes = catalog.attributes
        known_attribute_ids = {str(row["attribute_id"]) for row in attributes}
        main_choices = [choice for choice in _MAIN_PROPERTY_CHOICES if choice[1] in known_attribute_ids]
        substat_choices = [choice for choice in _SUBSTAT_PROPERTY_CHOICES if choice[1] in known_attribute_ids]
        window._weighted_suit_names = {
            str(row["suit_id"]): str(row.get("name_zh") or row["suit_id"]) for row in catalog.suits
        }
        window._weighted_property_names = {
            str(row["attribute_id"]): _RESULT_PROPERTY_LABELS.get(
                str(row["attribute_id"]),
                str(row.get("filter_name_zh") or row.get("display_name_zh") or row["attribute_id"]),
            )
            for row in attributes
        }
        window._weighted_property_percent = {
            str(row["attribute_id"]): bool(row.get("show_percent")) for row in attributes
        }
        window._weighted_main_property_by_label = dict(main_choices)
        window._weighted_substat_property_by_label = dict(substat_choices)
        window._weighted_item_names = {
            str(row["item_id"]): str(row.get("name_zh") or row["item_id"]) for row in catalog.equipment_items
        }
        window._weighted_item_icons = dict(catalog.item_icons)
        role_names = {str(row.get("name_zh") or row["character_id"]): int(row["character_id"]) for row in characters}
        window._weighted_role_ids = role_names
        window._weighted_role_names = {value: key for key, value in role_names.items()}
        window._weighted_default_suits = suit_defaults
        window._weighted_custom_character_ids = frozenset(
            int(row["character_id"]) for row in custom_characters
        )
        account_weights = ensure_account_character_weights(
            database_path,
            role_names.values(),
        )
        account_weights.update(custom_weights)
        window._weighted_default_property_weights = {
            character_id: dict(row.get("property_weights") or {}) for character_id, row in account_weights.items()
        }
        database_changed = getattr(window, "_weighted_persistence_database_path", None) != database_path
        window._weighted_preference_overrides = getattr(window, "_weighted_preference_overrides", {})
        suit_names = window._weighted_suit_names
        window.weighted_role_selector.load_roles(
            {
                name: {
                    "character_id": character_id,
                    "default_set": suit_names.get(suit_defaults.get(character_id), ""),
                }
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
            window.weighted_status_label.setText(tr("请选择角色并设置优先级。"))
    except Exception as exc:
        window.weighted_status_label.setText(tr("无法读取角色目录：{error}", error=exc))
