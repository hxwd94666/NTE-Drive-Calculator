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


def _load_weighted_persistence(window, database_path: Path) -> None:
    """Restore this account's v5 preferences, then rebuild its saved result."""

    window._weighted_persistence_database_path = database_path
    window._weighted_restore_token = object()
    window._weighted_allocation_preview = None
    window._weighted_allocation_saved_preview = None
    window.weighted_save_button.setEnabled(False)
    _set_weighted_equipment_actions_enabled(window, False)
    _clear_layout(window.weighted_result_layout)
    persistence = read_weighted_allocation_persistence(database_path)
    weights_changed = not _persistence_weights_match_account(window, persistence)
    _apply_weighted_persisted_preferences(window, persistence)
    if weights_changed:
        persistence = replace(persistence, restore_request=None)
    if persistence.restore_request is None:
        if weights_changed:
            window.weighted_status_label.setText(
                "账号词条权重已更新；已保留角色与空幕选择，请重新计算。"
            )
        elif persistence.profile_version is None:
            window.weighted_status_label.setText("请选择角色并设置优先级。")
        else:
            window.weighted_status_label.setText(
                f"已自动读取空幕偏好（v{persistence.profile_version}）；"
                "未找到与该版本完整对应的已保存方案。"
            )
        return
    token = object()
    window._weighted_restore_token = token
    window.weighted_status_label.setText(
        f"已自动读取空幕偏好（v{persistence.profile_version}），正在恢复已保存方案…"
    )
    worker = WorkerThread(
        target=lambda: restore_weighted_allocation_preview(persistence), parent=window,
    )
    window._weighted_allocation_restore_worker = worker
    worker.result_ready.connect(
        lambda preview: _on_weighted_restore_done(window, token, persistence, preview)
    )
    worker.error.connect(
        lambda error: _on_weighted_restore_error(window, token, persistence, error)
    )
    worker.start()


def _persistence_weights_match_account(
    window, persistence: WeightedAllocationPersistence,
) -> bool:
    defaults = getattr(window, "_weighted_default_property_weights", {})
    for row in persistence.characters:
        character_id = int(row["character_id"])
        account = {
            str(key): float(value)
            for key, value in defaults.get(character_id, {}).items()
            if float(value) > 0
        }
        saved = {
            str(key): float(value)
            for key, value in (row.get("property_weights") or {}).items()
            if float(value) > 0
        }
        if account != saved:
            return False
    return True


def _apply_weighted_persisted_preferences(
    window, persistence: WeightedAllocationPersistence,
) -> None:
    rows = sorted(persistence.characters, key=lambda row: int(row.get("ordinal", 0)))
    selected_rows = [
        row for row in rows
        if int(row["character_id"]) in getattr(window, "_weighted_role_names", {})
    ]
    selected = [window._weighted_role_names[int(row["character_id"])] for row in selected_rows]
    groups_by_id: dict[int, list[str]] = {}
    for row, name in zip(selected_rows, selected):
        groups_by_id.setdefault(int(row.get("priority_group", 0)), []).append(name)
    selector = window.weighted_role_selector
    selector.selected = selected
    selector.priority_links = priority_groups_to_links(selected, groups_by_id.values())
    selector._render_grid(selector.search.text())
    window._weighted_preference_overrides = {
        int(row["character_id"]): {
            "target_suit_id": row.get("target_suit_id"),
            "suit_requirement_mode": row.get("suit_requirement_mode", "none"),
            "core_main_property_id": row.get("core_main_property_id"),
            "property_weights": dict(
                getattr(window, "_weighted_default_property_weights", {}).get(
                    int(row["character_id"]), {}
                )
            ),
            "substat_priorities": list(row.get("substat_priorities") or ()),
            "property_limits": dict(row.get("property_limits") or {}),
        }
        for row in selected_rows
    }


def _on_weighted_restore_done(
    window, token: object, persistence: WeightedAllocationPersistence,
    preview: WeightedAllocationPreview | None,
) -> None:
    if (
        getattr(window, "_weighted_restore_token", None) is not token
        or Path(runtime.USER_DATABASE_PATH) != persistence.user_database_path
    ):
        return
    window._weighted_allocation_restore_worker = None
    if not isinstance(preview, WeightedAllocationPreview):
        return
    window._weighted_allocation_preview = preview
    window._weighted_allocation_saved_preview = preview
    window.weighted_save_button.setEnabled(bool(preview.result.unified.selected))
    render_weighted_allocation_result(window, preview.result, preview.context)
    _set_weighted_equipment_actions_enabled(window, bool(preview.result.unified.selected))
    window.weighted_status_label.setText(
        f"已自动读取保存方案：{len(preview.result.unified.selected)} 个角色，"
        f"空幕偏好 v{persistence.profile_version}。"
    )


def _on_weighted_restore_error(
    window, token: object, persistence: WeightedAllocationPersistence, error: str,
) -> None:
    if getattr(window, "_weighted_restore_token", None) is not token:
        return
    window._weighted_allocation_restore_worker = None
    window.weighted_status_label.setText(
        f"已自动读取空幕偏好（v{persistence.profile_version}），"
        f"但保存方案无法安全恢复：{error}"
    )


def _mark_weighted_preferences_dirty(window) -> None:
    window._weighted_restore_token = object()
    window._weighted_allocation_preview = None
    window._weighted_allocation_saved_preview = None
    if hasattr(window, "weighted_save_button"):
        window.weighted_save_button.setEnabled(False)
    _set_weighted_equipment_actions_enabled(window, False)
    if hasattr(window, "weighted_status_label"):
        window.weighted_status_label.setText("配置已修改，请重新计算。")


def _selection_rows(window) -> list[dict[str, Any]]:
    selected = window.weighted_role_selector.get_selected()
    groups = window.weighted_role_selector.get_priority_groups()
    group_index = {role: index for index, group in enumerate(groups) for role in group}
    overrides = getattr(window, "_weighted_preference_overrides", {})
    default_suits = getattr(window, "_weighted_default_suits", {})
    default_weights = getattr(window, "_weighted_default_property_weights", {})

    def row_for(name: str, ordinal: int) -> dict[str, Any]:
        character_id = window._weighted_role_ids[name]
        preference = overrides.get(character_id, {})
        target_suit_id = preference.get("target_suit_id", default_suits.get(character_id))
        priorities = list(preference.get("substat_priorities") or ())
        weights = dict(
            preference.get("property_weights", default_weights.get(character_id, {}))
        )
        return {
            "character_id": character_id, "ordinal": ordinal,
            "priority_group": group_index[name],
            "suit_requirement_mode": preference.get(
                "suit_requirement_mode", "four_piece" if target_suit_id else "none",
            ),
            "target_suit_id": target_suit_id,
            "core_main_property_id": preference.get("core_main_property_id"),
            "property_weights": weights,
            "substat_priorities": priorities,
            "property_limits": dict(preference.get("property_limits") or {}),
        }

    return [row_for(name, ordinal) for ordinal, name in enumerate(selected)]


def _build_single_select_row(selector: RoleSelector, title: str, choices: list[str], selected: list[str]) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(QLabel(title))
    combo = SearchableComboBox()
    selector._fill_search_combo(combo, choices)
    row.addWidget(combo, 1)
    add_button = QPushButton("添加")
    add_button.setObjectName("btnAction")
    add_button.setFixedWidth(60)
    clear_button = QPushButton("清空")
    clear_button.setObjectName("btnDanger")
    clear_button.setFixedWidth(74)
    row.addWidget(add_button)
    row.addWidget(clear_button)
    summary = selector._make_selected_summary_label()

    def refresh_summary() -> None:
        text = selected[0] if selected else "未选择"
        summary.setText(text)
        summary.setToolTip(text)

    def add_choice() -> None:
        value = resolve_priority_choice(choices, combo.currentText(), combo.currentData())
        if value in choices:
            selected[:] = [value]
            refresh_summary()
        combo.setCurrentIndex(-1)
        combo.setEditText("")

    add_button.clicked.connect(add_choice)
    clear_button.clicked.connect(lambda: (selected.clear(), refresh_summary()))
    layout.addLayout(row)
    layout.addWidget(summary)
    refresh_summary()
    return box


def _show_empty_curtain_preferences(window, role_name: str) -> None:
    character_id = window._weighted_role_ids[role_name]
    overrides = getattr(window, "_weighted_preference_overrides", {})
    current = overrides.get(character_id, {})
    dialog = QDialog(window)
    dialog.setWindowTitle(f"{role_name} · 管理")
    dialog.setMinimumSize(560, 320)
    layout = QVBoxLayout(dialog)
    layout.setSpacing(8)

    selector = window.weighted_role_selector
    suit_names = getattr(window, "_weighted_suit_names", {})
    suit_ids_by_name = {name: suit_id for suit_id, name in suit_names.items()}
    current_suit_id = current.get("target_suit_id", window._weighted_default_suits.get(character_id))
    config_box = QGroupBox("空幕配置")
    config_layout = QVBoxLayout(config_box)
    suit_row = QHBoxLayout()
    suit_row.addWidget(QLabel("套装："))
    suit_combo = SearchableComboBox()
    selector._fill_search_combo(
        suit_combo, list(suit_ids_by_name), suit_names.get(current_suit_id, ""),
    )
    suit_row.addWidget(suit_combo, 1)
    config_layout.addLayout(suit_row)
    layout.addWidget(config_box)

    main_by_label = getattr(window, "_weighted_main_property_by_label", {})
    substat_by_label = getattr(window, "_weighted_substat_property_by_label", {})
    main_label_by_id = {property_id: label for label, property_id in main_by_label.items()}
    substat_label_by_id = {property_id: label for label, property_id in substat_by_label.items()}
    selected_main = [main_label_by_id[current["core_main_property_id"]]] if current.get("core_main_property_id") in main_label_by_id else []
    selected_substats = [
        substat_label_by_id[property_id]
        for property_id in current.get("substat_priorities") or ()
        if property_id in substat_label_by_id
    ]
    stats_box = QGroupBox("词条自选")
    stats_layout = QVBoxLayout(stats_box)
    stats_layout.addWidget(_build_single_select_row(
        selector, "空幕主词条：", list(main_by_label), selected_main,
    ))
    stats_layout.addWidget(selector._build_multi_select_row(
        "空幕/驱动副词条：", list(substat_by_label), selected_substats, " > ",
    ))
    layout.addWidget(stats_box)

    current_weights = dict(
        current.get(
            "property_weights",
            getattr(window, "_weighted_default_property_weights", {}).get(character_id, {}),
        )
    )
    weights_box = QGroupBox("词条权重")
    weights_form = QFormLayout(weights_box)
    weights_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    weight_inputs: dict[str, NoWheelDoubleSpinBox] = {}
    for label, property_id in substat_by_label.items():
        spin = NoWheelDoubleSpinBox()
        spin.setRange(0.0, 10.0)
        spin.setDecimals(3)
        spin.setSingleStep(0.05)
        spin.setValue(float(current_weights.get(property_id, 0.0)))
        spin.setToolTip("0 表示该词条不参与评分；修改后保存到当前账号。")
        weights_form.addRow(f"{label}：", spin)
        weight_inputs[property_id] = spin
    # 默认仅露出五条权重，剩余词条在该区域内滚动查看，避免管理弹窗过长。
    weights_scroll = QScrollArea()
    weights_scroll.setObjectName("weightedRoleWeightScroll")
    weights_scroll.setWidgetResizable(True)
    weights_scroll.setFrameShape(QFrame.NoFrame)
    weights_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    weights_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    weights_scroll.setFixedHeight(210)
    weights_scroll.setWidget(weights_box)
    layout.addWidget(weights_scroll)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.Accepted:
        return
    selected_suit_name = resolve_priority_choice(
        list(suit_ids_by_name), suit_combo.currentText(), suit_combo.currentData(),
    )
    try:
        saved_weights = save_account_character_weights(
            runtime.USER_DATABASE_PATH,
            character_id,
            {
                property_id: spin.value()
                for property_id, spin in weight_inputs.items()
            },
        )
    except Exception as exc:
        QMessageBox.critical(dialog, "保存失败", f"无法保存账号词条权重：{exc}")
        return
    property_weights = dict(saved_weights.get("property_weights") or {})
    window._weighted_default_property_weights[character_id] = property_weights
    updated = dict(overrides)
    updated_preference = dict(current)
    updated_preference.update({
        "target_suit_id": suit_ids_by_name.get(selected_suit_name, current_suit_id),
        "suit_requirement_mode": "four_piece" if suit_ids_by_name.get(selected_suit_name, current_suit_id) else "none",
        "core_main_property_id": main_by_label.get(selected_main[0]) if selected_main else None,
        "substat_priorities": [substat_by_label[label] for label in selected_substats],
        "property_weights": property_weights,
    })
    updated[character_id] = updated_preference
    window._weighted_preference_overrides = updated
    _mark_weighted_preferences_dirty(window)


def _current_snapshot_and_profile(window) -> tuple[int, int, int]:
    rows = _selection_rows(window)
    if not rows:
        raise ValueError("请先选择至少一个角色。")
    with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
        snapshots = [row for row in dao.list_inventory_snapshots() if row.get("complete")]
        snapshot = next((row for row in snapshots if row.get("is_current")), None)
        if snapshot is None and snapshots:
            snapshot = max(snapshots, key=lambda row: int(row["snapshot_id"]))
        if snapshot is None:
            raise ValueError("没有可用的完整背包快照，请先完成背包同步。")
        profiles = dao.list_optimization_profiles()
        profile = next((row for row in profiles if row["name"] == _INTERNAL_PROFILE_NAME), None)
        if profile is None:
            profile = dao.create_optimization_profile(_INTERNAL_PROFILE_NAME, allocation_strategy="role_priority", characters=rows)
        else:
            version = profile["version"]
            if version.get("allocation_strategy") != "role_priority" or version.get("characters") != rows:
                dao.create_optimization_profile_version(int(profile["profile_id"]), allocation_strategy="role_priority", characters=rows)
            profile = dao.get_optimization_profile(int(profile["profile_id"]))
        version = profile["version"]
        return int(snapshot["snapshot_id"]), int(profile["profile_id"]), int(version["version_number"])


def start_weighted_allocation(window) -> None:
    window._weighted_restore_token = object()
    try:
        snapshot_id, profile_id, version = _current_snapshot_and_profile(window)
    except Exception as exc:
        QMessageBox.warning(window, "无法开始计算", str(exc))
        return
    request = WeightedAllocationRequest(
        Path(runtime.USER_DATABASE_PATH), snapshot_id, profile_id, version,
        _INTERNAL_TOP_K, include_role_top_k=False,
    )
    window.weighted_run_button.setEnabled(False)
    window._weighted_allocation_saved_preview = None
    _set_weighted_equipment_actions_enabled(window, False)
    window.weighted_status_label.setText("正在计算…")
    worker = WorkerThread(target=lambda: run_weighted_allocation(request), parent=window)
    window._weighted_allocation_worker = worker
    worker.result_ready.connect(lambda preview: _on_done(window, preview))
    worker.error.connect(lambda error: _on_error(window, error))
    worker.start()


def _on_done(window, preview: WeightedAllocationPreview) -> None:
    window.weighted_run_button.setEnabled(True)
    window._weighted_allocation_preview = preview
    window._weighted_allocation_saved_preview = None
    window.weighted_save_button.setEnabled(bool(preview.result.unified.selected))
    captured_at = preview.context.snapshot.captured_at_utc
    window.weighted_status_label.setText(f"计算完成。背包数据截至 {captured_at}")
    render_weighted_allocation_result(window, preview.result, preview.context)
    _set_weighted_equipment_actions_enabled(window, bool(preview.result.unified.selected))


def _on_error(window, error: str) -> None:
    window.weighted_run_button.setEnabled(True)
    window.weighted_status_label.setText(f"计算失败：{error}")
    QMessageBox.critical(window, "计算失败", error)


def start_weighted_allocation_save(
    window, after_save: Callable[[], None] | None = None,
) -> None:
    preview = _validated_weighted_preview(window, action_name="保存")
    if preview is None:
        return
    worker = WorkerThread(target=lambda: save_weighted_allocation_preview(preview), parent=window)
    # Keep the QThread reachable for its complete lifetime.  A local variable
    # can be garbage-collected while Qt is still executing the worker.
    window._weighted_allocation_save_worker = worker
    window.weighted_save_button.setEnabled(False)
    _set_weighted_equipment_actions_enabled(window, False)
    worker.result_ready.connect(
        lambda _ids: _on_weighted_save_done(window, preview, after_save)
    )
    worker.error.connect(lambda error: _on_weighted_save_error(window, error))
    worker.start()


def _on_weighted_save_done(
    window, preview: WeightedAllocationPreview, after_save: Callable[[], None] | None = None,
) -> None:
    window._weighted_allocation_save_worker = None
    window._weighted_allocation_saved_preview = preview
    window.weighted_save_button.setEnabled(True)
    _set_weighted_equipment_actions_enabled(window, True)
    window.weighted_status_label.setText("方案已保存。")
    if after_save is not None:
        after_save()


def _on_weighted_save_error(window, error: str) -> None:
    window._weighted_allocation_save_worker = None
    window.weighted_save_button.setEnabled(True)
    preview = getattr(window, "_weighted_allocation_preview", None)
    _set_weighted_equipment_actions_enabled(
        window,
        isinstance(preview, WeightedAllocationPreview) and bool(preview.result.unified.selected),
    )
    QMessageBox.critical(window, "保存失败", error)


def _set_weighted_equipment_actions_enabled(window, enabled: bool) -> None:
    window._weighted_equipment_actions_available = bool(enabled)
    for name in ("weighted_one_key_button", "weighted_automatic_button"):
        button = getattr(window, name, None)
        if button is not None:
            button.setEnabled(bool(enabled))
    for button in getattr(window, "_weighted_role_equip_buttons", ()):
        button.setEnabled(bool(enabled))


def _configured_equipment_apply_method(window) -> str:
    settings_reader = getattr(window, "_get_sync_settings", None)
    if callable(settings_reader):
        settings = settings_reader()
    else:
        settings = AccountSettingsService(runtime.USER_DATABASE_PATH).load("sync")
    method = str(settings.get("equipment_apply_method") or "").strip()
    if method not in {"nte_core", "gamepad"}:
        raise RuntimeError("装配执行方式无效，请先在设置中重新保存。")
    return method


def _perform_weighted_equipment_action(
    window, *, mode: str, role_name: str | None = None,
) -> None:
    try:
        method = "gamepad" if mode == "automatic" else _configured_equipment_apply_method(window)
    except Exception as exc:
        QMessageBox.warning(window, "无法装配", str(exc))
        return
    if role_name is None:
        preview = getattr(window, "_weighted_allocation_preview", None)
        role_names = [
            getattr(window, "_weighted_role_names", {}).get(option.character_id, str(option.character_id))
            for option in (
                preview.result.unified.selected
                if isinstance(preview, WeightedAllocationPreview)
                else ()
            )
        ]
        action = (
            inventory_page._preview_fast_assemble_all_roles
            if method == "nte_core"
            else inventory_page._preview_automatic_assemble_all_roles
        )
        action(window, role_names=role_names)
        return
    action = (
        inventory_page._preview_nte_core_assemble_role
        if method == "nte_core"
        else inventory_page._preview_automatic_assemble_role
    )
    action(window, role_name)


def _request_weighted_equipment(
    window, *, mode: str, role_name: str | None = None,
) -> None:
    preview = _validated_weighted_preview(window, action_name="装配")
    if preview is None:
        return
    action = lambda: _perform_weighted_equipment_action(
        window, mode=mode, role_name=role_name,
    )
    _run_after_weighted_preview_saved(window, preview, action)


def _request_weighted_replacement(window, role_name: str, assignment, role) -> None:
    preview = _validated_weighted_preview(window, action_name="替换")
    if preview is None:
        return
    weights = _display_weights(window, role)
    main_weights = _display_main_weights(window, role)
    role_option = next(
        (
            option for option in preview.result.unified.selected
            if any(item.uid == assignment.uid for item in option.assignments)
        ),
        None,
    )
    if role_option is None:
        QMessageBox.warning(window, "无法替换", "当前角色结果已变化，请重新计算。")
        return

    same_role_uids = {
        item.uid
        for item in role_option.assignments
        if item.uid != assignment.uid
    }
    temporary_owner_by_uid = {
        item.uid: option.character_id
        for option in preview.result.unified.selected
        for item in option.assignments
        if not item.virtual
    }
    role_names = getattr(window, "_weighted_role_names", {})
    asset_catalog = GameUiAssetCatalog(runtime.ASSET_DIR / "game_ui")

    def annotate_temporary_owner(
        item: dict[str, Any], uid: tuple[int, int],
    ) -> dict[str, Any]:
        owner_id = temporary_owner_by_uid.get(uid)
        if owner_id is None:
            return item
        result = dict(item)
        result["equipped"] = True
        result["equipped_character_id"] = owner_id
        result["equipped_character_name"] = str(
            role_names.get(owner_id, owner_id)
        )
        icon_path = asset_catalog.character_icon(owner_id)
        if icon_path is not None:
            result["equipped_character_icon_path"] = str(icon_path)
        return result

    candidate_map = {
        candidate.uid: candidate
        for candidate in preview.context.candidates
    }
    compatible = []
    for candidate in preview.context.candidates:
        if candidate.uid == assignment.uid or candidate.uid in same_role_uids:
            continue
        if candidate.kind != assignment.kind:
            continue
        if (
            not assignment.virtual
            and str(candidate.suit_id or "") != str(assignment.suit_id or "")
        ):
            continue
        if (
            assignment.kind == "module"
            and str(candidate.geometry or "").casefold()
            != str(assignment.geometry or "").casefold()
        ):
            continue
        compatible.append(candidate)
    if not compatible:
        QMessageBox.information(
            window,
            "替换优化",
            "当前计算临时候选池中没有可替换的同套装、同形状装备。",
        )
        return

    source_rows = [
        annotate_temporary_owner(
            _allocation_candidate_row(
                window, item, candidate_map.get(item.uid)
            ),
            item.uid,
        )
        for item in role_option.assignments
    ]
    with StaticGameDataDao() as static_dao:
        projected = project_equipment_items_to_max_level(
            [
                *source_rows,
                *(
                    annotate_temporary_owner(
                        _allocation_candidate_row(
                            window, assignment, candidate
                        ),
                        candidate.uid,
                    )
                    for candidate in compatible
                ),
            ],
            static_dao,
        )
    source_count = len(source_rows)
    projected_current_items = projected[:source_count]
    projected_candidates = projected[source_count:]
    current_item = next(
        (
            item
            for item in projected_current_items
            if (
                int(item.get("uid_slot") or 0),
                int(item.get("uid_serial") or 0),
            ) == assignment.uid
        ),
        None,
    )
    if current_item is None:
        QMessageBox.warning(window, "无法替换", "当前装备不在计算临时候选池中。")
        return

    def item_score(item: Mapping[str, Any]) -> float:
        sub_stats = {
            str((stat.get("names") or {}).get("zh_cn") or stat.get("property_id") or ""):
            float(stat.get("value") or 0.0)
            for stat in item.get("sub_stats") or ()
        }
        quality = _legacy_quality(str(item.get("quality") or ""))
        if str(item.get("kind") or "") == "core":
            main_stat = next(
                (
                    str(
                        (stat.get("names") or {}).get("zh_cn")
                        or stat.get("property_id")
                        or ""
                    )
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

    context_key = "_weighted_replacement"
    detail = load_official_role_detail(
        preview.user_database_path,
        role_option.character_id,
    )
    context = {
        "title": "词条配装临时结果",
        "items": tuple(projected_current_items),
        "available": True,
    }
    full_detail = {
        **detail,
        "equipment_contexts": {
            **(detail.get("equipment_contexts") or {}),
            context_key: context,
        },
    }
    current_gain = calculate_official_role_item_gain(
        full_detail,
        context_key,
        current_item,
    )
    current_direct_damage_score = (
        float(current_gain["gain_percent"]) if current_gain else None
    )

    def direct_damage_score(
        candidate_item: Mapping[str, Any],
    ) -> float | None:
        replaced = tuple(
            candidate_item
            if (
                int(item.get("uid_slot") or 0),
                int(item.get("uid_serial") or 0),
            ) == assignment.uid
            else item
            for item in projected_current_items
        )
        candidate_detail = {
            **full_detail,
            "equipment_contexts": {
                **full_detail["equipment_contexts"],
                context_key: {**context, "items": replaced},
            },
        }
        item_gain = calculate_official_role_item_gain(
            candidate_detail,
            context_key,
            candidate_item,
        )
        return float(item_gain["gain_percent"]) if item_gain else None

    def card(
        item: Mapping[str, Any],
        *,
        score: float,
        direct_damage_score: float | None,
        payload,
    ) -> EquipmentReplacementCard:
        view = warehouse_item_view(item)
        icon_path = getattr(window, "_weighted_item_icons", {}).get(
            str(item.get("item_id") or "")
        )
        if icon_path:
            view["item_icon_path"] = icon_path
        area = (
            15
            if str(item.get("kind") or "") == "core"
            else int(item.get("grid_count") or 0)
        )
        return EquipmentReplacementCard(
            key=f"{item.get('uid_slot')}:{item.get('uid_serial')}",
            item_view=view,
            score=score,
            grade=legacy_results._calc_grade(window, score, area),
            direct_damage_score=direct_damage_score,
            payload=payload,
            note=(
                f"将从 {view.get('equipped_character_name')} 的临时方案借用，"
                "并为其原槽位补入金色占位装备。"
                if view.get("equipped_character_name")
                else ""
            ),
        )

    current_score = item_score(current_item)
    current_card = card(
        current_item,
        score=current_score,
        direct_damage_score=current_direct_damage_score,
        payload=None,
    )
    choices = []
    for candidate, item in zip(compatible, projected_candidates):
        score = item_score(item)
        choices.append(card(
            item,
            score=score,
            direct_damage_score=direct_damage_score(item),
            payload={
                "_uid_slot": candidate.uid_slot,
                "_uid_serial": candidate.uid_serial,
                "score": score,
            },
        ))
    choices.sort(
        key=lambda choice: float(choice.score or 0.0),
        reverse=True,
    )

    show_equipment_replacement_dialog(
        window,
        title=f"{role_name} · 替换优化",
        role_name=role_name,
        summary=(
            "候选与持有者只来自当前词条配装临时结果，不读取活动配装库；"
            "借用其他角色装备后会在其原槽位生成可继续替换的金色占位装备。"
        ),
        current=current_card,
        candidates=choices[:30],
        on_confirm=lambda choice: _on_weighted_replacement_done(
            window,
            preview,
            assignment.uid,
            choice.payload,
            float(choice.score or 0.0),
            current_score,
        ),
    )


def _validated_weighted_preview(
    window,
    *,
    action_name: str,
) -> WeightedAllocationPreview | None:
    """Return the current account's complete preview for save-dependent actions."""

    preview = getattr(window, "_weighted_allocation_preview", None)
    if not isinstance(preview, WeightedAllocationPreview) or not preview.result.unified.selected:
        QMessageBox.information(
            window, f"无法{action_name}", "请先完成一次有效的配装计算。"
        )
        return None
    if preview.user_database_path != Path(runtime.USER_DATABASE_PATH):
        QMessageBox.warning(
            window, "账号已切换", f"请在当前账号重新计算后再{action_name}。"
        )
        return None
    return preview


def _run_after_weighted_preview_saved(
    window,
    preview: WeightedAllocationPreview,
    action: Callable[[], None],
) -> None:
    """Run an action only after the exact in-memory preview is persisted."""

    if getattr(window, "_weighted_allocation_saved_preview", None) is preview:
        action()
        return
    start_weighted_allocation_save(window, after_save=action)


def _on_weighted_replacement_done(
    window,
    preview: WeightedAllocationPreview,
    old_uid: tuple[int, int],
    selected: dict[str, Any],
    selected_score: float,
    current_score: float,
) -> None:
    if getattr(window, "_weighted_allocation_preview", None) is not preview:
        raise RuntimeError("当前计算结果已变化，请重新打开替换窗口。")
    new_uid = (int(selected["_uid_slot"]), int(selected["_uid_serial"]))
    updated_preview = replace_weighted_allocation_assignment(
        preview,
        old_uid=old_uid,
        new_uid=new_uid,
        new_score=float(selected_score),
    )
    save_weighted_allocation_preview(updated_preview)
    window._weighted_allocation_preview = updated_preview
    window._weighted_allocation_saved_preview = updated_preview
    render_weighted_allocation_result(
        window, updated_preview.result, updated_preview.context
    )
    _set_weighted_equipment_actions_enabled(window, True)
    window.weighted_status_label.setText(
        "替换已保存为新的 SQLite 配装方案；重新计算会重新生成推荐方案。"
    )


def render_weighted_allocation_result(
    window, result: AllocationSolveResult, context: AllocationContext | None = None,
) -> None:
    _clear_layout(window.weighted_result_layout)
    card = window._card("计算结果")
    card_layout = card.layout()
    window._weighted_role_equip_buttons = []
    candidates = {candidate.uid: candidate for candidate in (context.candidates if context else ())}
    role_preferences = {role.character_id: role for role in (context.roles if context else ())}
    shape_resources = _shape_resource_ids(context)
    for option in result.unified.selected:
        card_layout.addWidget(_role_option_card(
            window, option, candidates, role_preferences.get(option.character_id), shape_resources,
        ))
    if result.unified.unassigned_character_ids:
        card_layout.addWidget(QLabel(_unassigned_reason(window, context, result.unified.unassigned_character_ids)))
    window.weighted_result_layout.addWidget(card)


def _role_option_card(
    window, option: RoleAllocationOption, candidates: dict = None, role=None,
    shape_resources: dict[str, str] | None = None,
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
    summary_panel = _official_bonus_summary_panel(
        window,
        name,
        option.character_id,
        summary_core,
        summary_drives,
        role,
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
    try:
        detail = load_official_role_detail(
            runtime.USER_DATABASE_PATH,
            option.character_id,
        )
    except (OSError, ValueError):
        return {}
    context_key = "_weighted_result"
    detail = {
        **detail,
        "equipment_contexts": {
            **(detail.get("equipment_contexts") or {}),
            context_key: {
                "title": "词条配装结果",
                "items": tuple(items_by_uid.values()),
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
) -> dict[str, tuple[AttributeSummaryRow, ...]]:
    selected = [
        item
        for item in (loadout.core, *loadout.drives)
        if item is not None
    ]
    detail = load_official_role_detail(
        runtime.USER_DATABASE_PATH,
        loadout.character_id,
    )
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
) -> QWidget:
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
