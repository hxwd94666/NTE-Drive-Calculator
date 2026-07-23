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
    QFormLayout, QScrollArea, QVBoxLayout, QWidget,
)

from src.app import runtime
from src.app.theme import theme_color, theme_rgba, themed_style
from src.app.workers import WorkerThread
from src.features.allocation.priority_groups import priority_groups_to_links
from src.features.allocation.role_selector import RoleSelector, resolve_priority_choice
from src.features.allocation import results_view as legacy_results
from src.features.inventory import page as inventory_page
from src.features.weighted_allocation.runner import (
    WeightedAllocationPersistence, WeightedAllocationPreview, WeightedAllocationRequest,
    read_weighted_allocation_persistence, restore_weighted_allocation_preview,
    replace_weighted_allocation_assignment, run_weighted_allocation,
    save_weighted_allocation_preview,
)
from src.services.allocation_solver import AllocationSolveResult, RoleAllocationOption
from src.services.allocation_context import AllocationContext
from src.services.character_weight_service import (
    ensure_account_character_weights, save_account_character_weights,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.official_equipment_bonus_service import calculate_official_equipment_stats
from src.services.sqlite_allocation_inventory import (
    AllocationInventoryProjectionError, legacy_shape_id,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
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
        with UserDataDao(runtime.USER_DATABASE_PATH) as dao:
            settings = dao.get_sync_settings()
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
    uid = f"nte-{'core' if assignment.kind == 'core' else 'module'}-{assignment.uid[0]}-{assignment.uid[1]}"
    role_option = next(
        (
            option for option in preview.result.unified.selected
            if any(item.uid == assignment.uid for item in option.assignments)
        ),
        None,
    )
    assignment_scores = {
        f"nte-{item.kind}-{item.uid[0]}-{item.uid[1]}": float(item.score)
        for item in (role_option.assignments if role_option is not None else ())
    }

    def action() -> None:
        inventory_page._optimize_saved_equipment(
            window,
            role_name,
            "tape" if assignment.kind == "core" else "drive",
            uid,
            weights_override=weights,
            main_weights_override=main_weights,
            rank_by_damage=False,
            core_term="空幕",
            assignment_scores_override=assignment_scores,
            exclude_used_by_others=True,
            replacement_persister=lambda selected, selected_score, current_score: _on_weighted_replacement_done(
                window, preview, assignment.uid, selected, selected_score, current_score,
            ),
        )

    _run_after_weighted_preview_saved(window, preview, action)


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

    summary_panel = _official_bonus_summary_panel(window, name, option, candidates or {}, role)
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
    weights = _display_weights(window, role)
    if core:
        layout.addWidget(legacy_results._section_label(window, "空幕:"))
        layout.addWidget(_legacy_equipment_card(
            window, core, candidates or {}, weights, shape_resources or {},
            replacement_callback=lambda current=core: _request_weighted_replacement(
                window, name, current, role,
            ),
        ))
    else:
        layout.addWidget(QLabel(_missing_core_text(window, role)))
    if modules:
        layout.addWidget(legacy_results._section_label(window, f"驱动 ({len(modules)}个):"))
        for module in modules:
            layout.addWidget(_legacy_equipment_card(
                window, module, candidates or {}, weights, shape_resources or {},
                replacement_callback=lambda current=module: _request_weighted_replacement(
                    window, name, current, role,
                ),
            ))
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


def _property_summary_panel(summary: str) -> QWidget:
    panel = QGroupBox("空幕属性汇总")
    panel.setStyleSheet(themed_style(
        "QGroupBox{background:#161b22;border:1px solid #30363d;"
        "border-radius:8px;margin-top:8px;padding:12px}"
    ))
    layout = QVBoxLayout(panel)
    label = QLabel(summary)
    label.setWordWrap(True)
    layout.addWidget(label)
    return panel


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


def _official_summary_rows(
    window, option: RoleAllocationOption, candidates: dict, role=None,
) -> list[tuple[str, str, float, bool]]:
    selected = [
        candidate
        for assignment in option.assignments
        if (candidate := candidates.get(assignment.uid)) is not None
    ]
    extra_shape_label = getattr(role, "extra_shape_label", "") if role is not None else ""
    if not isinstance(extra_shape_label, str):
        extra_shape_label = ""
    extra_shape_buffs = getattr(role, "extra_shape_buffs", ()) if role is not None else ()
    if not isinstance(extra_shape_buffs, (Mapping, tuple, list)):
        extra_shape_buffs = ()
    totals = calculate_official_equipment_stats(
        selected,
        extra_shape_label=extra_shape_label,
        extra_shape_buffs=extra_shape_buffs,
        property_percent=getattr(window, "_weighted_property_percent", {}) or {},
    )
    labels = getattr(window, "_weighted_property_names", {})
    return [
        (
            total.property_id,
            labels.get(total.property_id, total.property_id),
            _display_stat_value(total.value, total.percent),
            total.percent,
        )
        for total in totals
    ]


def _summary_value_text(value: float, percent: bool) -> str:
    number = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"+{number}{'%' if percent else ''}"


def _summary_row(
    window, property_id: str, label: str, value: float, percent: bool, weight: float,
) -> QWidget:
    row = QFrame()
    row.setFixedHeight(26)
    row.setStyleSheet(themed_style(
        "QFrame{background:#161b22;border:1px solid #21262d;border-radius:5px;padding:2px 6px}"
    ))
    layout = QHBoxLayout(row)
    layout.setContentsMargins(6, 1, 6, 1)
    color = legacy_results._stat_c(window, weight) if weight > 0 else theme_color("#8b949e")
    name = QLabel(label)
    name.setStyleSheet(f"font-size:10px;font-weight:700;color:{color};border:none;background:transparent")
    val = QLabel(_summary_value_text(value, percent))
    val.setStyleSheet("font-size:10px;font-weight:800;color:#f0f6fc;border:none;background:transparent")
    val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    layout.addWidget(name, 1)
    layout.addWidget(val)
    return row


def _show_official_summary_dialog(window, role_name: str, rows, weights: dict[str, float]) -> None:
    dialog = QDialog(window if isinstance(window, QWidget) else None)
    dialog.setWindowTitle(f"{role_name} 空幕属性汇总")
    dialog.setMinimumSize(360, 420)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(8)
    for property_id, label, value, percent in rows:
        layout.addWidget(
            _summary_row(
                window, property_id, label, value, percent, weights.get(property_id, 0.0),
            )
        )
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()


def _official_bonus_summary_panel(
    window, role_name: str, option: RoleAllocationOption, candidates: dict, role,
) -> QWidget | None:
    rows = _official_summary_rows(window, option, candidates, role)
    weights = dict(getattr(role, "effective_property_weights", ()) if role else ())
    rows.sort(key=lambda item: (-weights.get(item[0], 0.0), item[1]))
    if not rows:
        return None
    panel = QFrame()
    panel.setMinimumWidth(300)
    panel.setStyleSheet(themed_style(
        "QFrame{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:6px}"
    ))
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(7, 5, 7, 5)
    layout.setSpacing(4)
    header = QHBoxLayout()
    mode = QPushButton("空幕属性汇总")
    mode.setCheckable(True)
    mode.setChecked(True)
    mode.setStyleSheet(themed_style(
        "QPushButton{background:#1f6feb22;color:#58a6ff;border:1px solid #58a6ff;"
        "border-radius:6px;font-size:10px;font-weight:700;padding:2px 6px;min-height:22px}"
    ))
    header.addWidget(mode)
    if len(rows) > 5:
        more = legacy_results._bonus_more_button(
            lambda _checked=False: _show_official_summary_dialog(window, role_name, rows, weights)
        )
        header.addWidget(more)
    header.addStretch()
    layout.addLayout(header)
    for property_id, label, value, percent in rows[:5]:
        layout.addWidget(
            _summary_row(
                window, property_id, label, value, percent, weights.get(property_id, 0.0),
            )
        )
    layout.addStretch()
    return panel


def _legacy_equipment_card(
    window, assignment, candidates: dict, weights: dict, shape_resources: dict[str, str],
    replacement_callback=None,
) -> QWidget:
    source = _legacy_equipment_source(window, assignment, candidates, shape_resources)
    is_core = assignment.kind == "core"
    label = source["display_name"] if is_core else _geometry_display_name(assignment.geometry)
    return legacy_results._equip_card(
        window, label, source["main_stats"], source["sub_stats"], source["shape_id"], "", weights,
        (assignment.score, legacy_results._calc_grade(window, assignment.score, source["area"])),
        source["quality"], replacement_callback=replacement_callback,
        card_variant="result", item_icon_path=source["icon_path"],
    )


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
