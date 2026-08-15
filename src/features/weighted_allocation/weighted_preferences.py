# 提供只含角色优先级、计算和统一结果的词条配装页面。
"""Minimal role-priority UI for the audited weighted-allocation facade."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.workers import WorkerThread
from src.domain.grade_limits import GRADE_LADDER
from src.features.allocation.priority_groups import priority_groups_to_links
from src.features.allocation.role_selector import RoleSelector, resolve_priority_choice
from src.features.weighted_allocation.help_text import (
    WEIGHTED_CRIT_THRESHOLD_HELP,
    WEIGHTED_SET_EFFECT_HELP,
    WEIGHTED_STAT_FILTER_HELP,
)
from src.features.weighted_allocation.runner import (
    WeightedAllocationPersistence,
    WeightedAllocationPreview,
    read_weighted_allocation_persistence,
    restore_weighted_allocation_preview,
)
from src.solver.set_effects import (
    FOUR_PIECE,
    NO_EFFECT,
    TWO_PIECE,
    normalize_set_effect_mode,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from src.ui.widgets import SearchableComboBox
from .dependencies import weighted_allocation_dependencies


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


def render_weighted_allocation_result(*args, **kwargs):
    from .weighted_result_view import render_weighted_allocation_result as render

    return render(*args, **kwargs)


def _set_weighted_equipment_actions_enabled(window, enabled: bool) -> None:
    """延迟引用工作流，避免偏好模块与工作流模块形成导入环。"""
    from .weighted_workflow import _set_weighted_equipment_actions_enabled as setter

    setter(window, enabled)


def _load_weighted_persistence(window, database_path: Path) -> None:
    """Restore this account's v5 preferences, then rebuild its saved result."""

    window._weighted_persistence_database_path = database_path
    window._weighted_restore_token = object()
    window._weighted_calculation_token = object()
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
            window.weighted_status_label.setText("账号词条权重已更新；已保留角色与卡带选择，请重新计算。")
        elif persistence.profile_version is None:
            window.weighted_status_label.setText("请选择角色并设置优先级。")
        else:
            window.weighted_status_label.setText(
                f"已自动读取卡带偏好（v{persistence.profile_version}）；未找到与该版本完整对应的已保存方案。"
            )
        return
    token = object()
    window._weighted_restore_token = token
    window.weighted_status_label.setText(f"已自动读取卡带偏好（v{persistence.profile_version}），正在恢复已保存方案…")
    worker = WorkerThread(
        target=lambda: restore_weighted_allocation_preview(persistence),
        parent=window,
    )
    window._weighted_allocation_restore_worker = worker
    worker.result_ready.connect(lambda preview: _on_weighted_restore_done(window, token, persistence, preview))
    worker.error.connect(lambda error: _on_weighted_restore_error(window, token, persistence, error))
    worker.start()


def _persistence_weights_match_account(
    window,
    persistence: WeightedAllocationPersistence,
) -> bool:
    defaults = getattr(window, "_weighted_default_property_weights", {})
    for row in persistence.characters:
        character_id = int(row["character_id"])
        account = {str(key): float(value) for key, value in defaults.get(character_id, {}).items() if float(value) > 0}
        saved = {
            str(key): float(value) for key, value in (row.get("property_weights") or {}).items() if float(value) > 0
        }
        if account != saved:
            return False
    return True


def _apply_weighted_persisted_preferences(
    window,
    persistence: WeightedAllocationPersistence,
) -> None:
    rows = sorted(persistence.characters, key=lambda row: int(row.get("ordinal", 0)))
    selected_rows = [row for row in rows if int(row["character_id"]) in getattr(window, "_weighted_role_names", {})]
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
                getattr(window, "_weighted_default_property_weights", {}).get(int(row["character_id"]), {})
            ),
            "substat_priorities": list(row.get("substat_priorities") or ()),
            "substat_blacklist": list(row.get("substat_blacklist") or ()),
            "equal_priority": bool(row.get("equal_priority", False)),
            "ignore_grade_limit": bool(row.get("ignore_grade_limit", False)),
            "min_grade_limit": str(row.get("min_grade_limit") or "A"),
            "crit_threshold": row.get("crit_threshold"),
            "property_limits": dict(row.get("property_limits") or {}),
        }
        for row in selected_rows
    }


def _on_weighted_restore_done(
    window,
    token: object,
    persistence: WeightedAllocationPersistence,
    preview: WeightedAllocationPreview | None,
) -> None:
    if (
        getattr(window, "_weighted_restore_token", None) is not token
        or weighted_allocation_dependencies(window).user_database_path != persistence.user_database_path
    ):
        return
    window._weighted_allocation_restore_worker = None
    if not isinstance(preview, WeightedAllocationPreview):
        return
    window._weighted_allocation_preview = preview
    window._weighted_allocation_saved_preview = preview
    window.weighted_save_button.setEnabled(bool(preview.result.unified.selected))
    render_weighted_allocation_result(
        window,
        preview,
    )
    _set_weighted_equipment_actions_enabled(window, bool(preview.result.unified.selected))
    window.weighted_status_label.setText(
        f"已自动读取保存方案：{len(preview.result.unified.selected)} 个角色，卡带偏好 v{persistence.profile_version}。"
    )


def _on_weighted_restore_error(
    window,
    token: object,
    persistence: WeightedAllocationPersistence,
    error: str,
) -> None:
    if getattr(window, "_weighted_restore_token", None) is not token:
        return
    window._weighted_allocation_restore_worker = None
    window.weighted_status_label.setText(
        f"已自动读取卡带偏好（v{persistence.profile_version}），但保存方案无法安全恢复：{error}"
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


def _effective_core_main_property_id(
    character_id: int,
    preference: dict[str, Any],
    default_mag_character_ids: frozenset[int] = frozenset(),
) -> str | None:
    """Return only an explicitly saved main-stat selection."""

    del character_id, default_mag_character_ids
    selected = preference.get("core_main_property_id")
    return str(selected) if selected else None


def _effective_substat_priorities(
    character_id: int,
    preference: dict[str, Any],
    default_mag_character_ids: frozenset[int] = frozenset(),
) -> list[str]:
    """Return only explicitly saved substat preferences."""

    del character_id, default_mag_character_ids
    return [
        str(value)
        for value in preference.get("substat_priorities") or ()
        if value
    ]


def _selection_rows(window) -> list[dict[str, Any]]:
    selected = window.weighted_role_selector.get_selected()
    groups = window.weighted_role_selector.get_priority_groups()
    group_index = {role: index for index, group in enumerate(groups) for role in group}
    overrides = getattr(window, "_weighted_preference_overrides", {})
    default_suits = getattr(window, "_weighted_default_suits", {})
    default_weights = getattr(window, "_weighted_default_property_weights", {})
    default_mag_character_ids = getattr(
        window.weighted_role_selector,
        "default_mag_character_ids",
        frozenset(),
    )

    def row_for(name: str, ordinal: int) -> dict[str, Any]:
        character_id = window._weighted_role_ids[name]
        preference = overrides.get(character_id, {})
        target_suit_id = preference.get("target_suit_id", default_suits.get(character_id))
        if (
            character_id in getattr(window, "_weighted_custom_character_ids", frozenset())
            and not target_suit_id
        ):
            raise ValueError(f"[{name}] 的套装为空，请先在角色管理中选择套装。")
        priorities = _effective_substat_priorities(
            character_id,
            preference,
            default_mag_character_ids,
        )
        blacklist = list(preference.get("substat_blacklist") or ())
        weights = dict(preference.get("property_weights", default_weights.get(character_id, {})))
        return {
            "character_id": character_id,
            "ordinal": ordinal,
            "priority_group": group_index[name],
            "suit_requirement_mode": preference.get(
                "suit_requirement_mode",
                "four_piece" if target_suit_id else "none",
            ),
            "target_suit_id": target_suit_id,
            "core_main_property_id": _effective_core_main_property_id(
                character_id,
                preference,
                default_mag_character_ids,
            ),
            "property_weights": weights,
            "substat_priorities": priorities,
            "substat_blacklist": blacklist,
            "equal_priority": bool(preference.get("equal_priority", False)),
            "ignore_grade_limit": bool(
                preference.get("ignore_grade_limit", False)
            ),
            "min_grade_limit": str(
                preference.get("min_grade_limit") or "A"
            ).upper(),
            "crit_threshold": preference.get("crit_threshold"),
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
    config_box = QGroupBox("卡带配置")
    config_layout = QVBoxLayout(config_box)
    suit_row = QHBoxLayout()
    suit_row.addWidget(QLabel("套装："))
    suit_combo = SearchableComboBox()
    selector._fill_search_combo(
        suit_combo,
        list(suit_ids_by_name),
        suit_names.get(current_suit_id, ""),
    )
    suit_row.addWidget(suit_combo, 1)
    config_layout.addLayout(suit_row)
    layout.addWidget(config_box)

    main_by_label = getattr(window, "_weighted_main_property_by_label", {})
    substat_by_label = getattr(window, "_weighted_substat_property_by_label", {})
    main_label_by_id = {property_id: label for label, property_id in main_by_label.items()}
    substat_label_by_id = {property_id: label for label, property_id in substat_by_label.items()}
    default_mag_character_ids = getattr(
        selector,
        "default_mag_character_ids",
        frozenset(),
    )
    effective_main_property_id = _effective_core_main_property_id(
        character_id,
        current,
        default_mag_character_ids,
    )
    selected_main = (
        [main_label_by_id[effective_main_property_id]]
        if effective_main_property_id in main_label_by_id
        else []
    )
    selected_substats = [
        substat_label_by_id[property_id]
        for property_id in _effective_substat_priorities(
            character_id,
            current,
            default_mag_character_ids,
        )
        if property_id in substat_label_by_id
    ]
    selected_blacklist = [
        substat_label_by_id[property_id]
        for property_id in current.get("substat_blacklist") or ()
        if property_id in substat_label_by_id
    ]
    stats_box = QGroupBox("词条自选")
    stats_layout = QVBoxLayout(stats_box)
    stats_layout.addWidget(
        _build_single_select_row(
            selector,
            "卡带主词条：",
            list(main_by_label),
            selected_main,
        )
    )
    stats_layout.addWidget(
        selector._build_multi_select_row(
            "卡带/驱动副词条：",
            list(substat_by_label),
            selected_substats,
            " > ",
            empty_text="未选择",
        )
    )
    stats_layout.addWidget(
        selector._build_multi_select_row(
            "副词条黑名单：",
            list(substat_by_label),
            selected_blacklist,
            "、",
            empty_text="未选择",
        )
    )
    stat_options = QHBoxLayout()
    stat_options.setContentsMargins(0, 4, 0, 0)
    stat_options.setSpacing(12)
    equal_priority = QCheckBox("副词条优先级一致")
    equal_priority.setChecked(bool(current.get("equal_priority", False)))
    ignore_grade_limit = QCheckBox("不限制评分等级")
    ignore_grade_limit.setChecked(bool(current.get("ignore_grade_limit", False)))
    min_grade_label = QLabel("最低生效等级：")
    min_grade_combo = QComboBox()
    min_grade_combo.setFixedWidth(84)
    for grade in GRADE_LADDER:
        min_grade_combo.addItem(grade, grade)
    current_min_grade = str(current.get("min_grade_limit") or "A").upper()
    min_grade_index = min_grade_combo.findData(current_min_grade)
    min_grade_combo.setCurrentIndex(
        min_grade_index
        if min_grade_index >= 0
        else min_grade_combo.findData("A")
    )
    min_grade_combo.setEnabled(not ignore_grade_limit.isChecked())
    stat_help = QPushButton("?")
    stat_help.setObjectName("btnHelp")
    stat_help.setFixedSize(24, 24)
    stat_help.clicked.connect(
        lambda: selector._show_help("词条筛选与优先级说明", WEIGHTED_STAT_FILTER_HELP)
    )
    stat_options.addWidget(equal_priority)
    stat_options.addWidget(ignore_grade_limit)
    stat_options.addWidget(min_grade_label)
    stat_options.addWidget(min_grade_combo)
    stat_options.addWidget(stat_help)
    stat_options.addStretch(1)
    ignore_grade_limit.toggled.connect(
        lambda _checked: min_grade_combo.setEnabled(
            not ignore_grade_limit.isChecked()
        )
    )
    stats_layout.addLayout(stat_options)
    priority_tip = QLabel(
        "副词条黑名单最先从驱动候选池硬过滤，不淘汰卡带；"
        "套装与卡带主词条随后硬过滤；"
        "副词条自选先使用连续前缀的最深候选池，"
        "组合无解时逐层放宽，最后回到完整候选池。"
    )
    priority_tip.setWordWrap(True)
    stats_layout.addWidget(priority_tip)
    layout.addWidget(stats_box)

    other_box = QGroupBox("其他配置")
    other_layout = QVBoxLayout(other_box)
    effect_row = QHBoxLayout()
    effect_row.addWidget(QLabel("套装效果："))
    effect_combo = QComboBox()
    effect_combo.addItem("四件套", FOUR_PIECE)
    effect_combo.addItem("二件套", TWO_PIECE)
    effect_combo.addItem("无效果", NO_EFFECT)
    current_effect = normalize_set_effect_mode(
        str(current.get("suit_requirement_mode") or FOUR_PIECE)
    )
    effect_index = effect_combo.findData(current_effect)
    effect_combo.setCurrentIndex(effect_index if effect_index >= 0 else 0)
    effect_row.addWidget(effect_combo, 1)
    effect_help = QPushButton("?")
    effect_help.setObjectName("btnHelp")
    effect_help.setFixedSize(24, 24)
    effect_help.clicked.connect(
        lambda: selector._show_help("套装效果说明", WEIGHTED_SET_EFFECT_HELP)
    )
    effect_row.addWidget(effect_help)
    other_layout.addLayout(effect_row)

    crit_row = QHBoxLayout()
    crit_row.addWidget(QLabel("暴击率最小值："))
    crit_threshold = QLineEdit()
    crit_threshold.setFixedWidth(84)
    crit_threshold.setValidator(QIntValidator(0, 100, crit_threshold))
    if current.get("crit_threshold") is not None:
        crit_threshold.setText(f"{float(current['crit_threshold']):g}")
    crit_row.addWidget(crit_threshold)
    crit_row.addWidget(QLabel("%"))
    crit_help = QPushButton("?")
    crit_help.setObjectName("btnHelp")
    crit_help.setFixedSize(24, 24)
    crit_help.clicked.connect(
        lambda: selector._show_help("暴击率最小值", WEIGHTED_CRIT_THRESHOLD_HELP)
    )
    crit_row.addWidget(crit_help)
    crit_row.addStretch(1)
    other_layout.addLayout(crit_row)
    layout.addWidget(other_box)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.Accepted:
        return
    selected_suit_name = resolve_priority_choice(
        list(suit_ids_by_name),
        suit_combo.currentText(),
        suit_combo.currentData(),
    )
    selected_suit_id = suit_ids_by_name.get(selected_suit_name, current_suit_id)
    selected_effect = (
        effect_combo.currentData() if selected_suit_id is not None else NO_EFFECT
    )
    crit_threshold_text = crit_threshold.text().strip()
    property_weights = dict(
        getattr(window, "_weighted_default_property_weights", {}).get(character_id, {})
    )
    updated = dict(overrides)
    updated_preference = dict(current)
    updated_preference.update(
        {
            "target_suit_id": selected_suit_id,
            "suit_requirement_mode": selected_effect,
            "core_main_property_id": main_by_label.get(selected_main[0]) if selected_main else None,
            "substat_priorities": [substat_by_label[label] for label in selected_substats],
            "substat_blacklist": [substat_by_label[label] for label in selected_blacklist],
            "equal_priority": equal_priority.isChecked(),
            "ignore_grade_limit": ignore_grade_limit.isChecked(),
            "min_grade_limit": str(min_grade_combo.currentData() or "A"),
            "crit_threshold": (
                float(crit_threshold_text) if crit_threshold_text else None
            ),
            "property_weights": property_weights,
        }
    )
    updated[character_id] = updated_preference
    window._weighted_preference_overrides = updated
    _mark_weighted_preferences_dirty(window)


def _current_snapshot_and_profile(window) -> tuple[int, int, int]:
    rows = _selection_rows(window)
    if not rows:
        raise ValueError("请先选择至少一个角色。")
    with UserDataDao(weighted_allocation_dependencies(window).user_database_path) as dao:
        snapshots = [row for row in dao.list_inventory_snapshots() if row.get("complete")]
        snapshot = next((row for row in snapshots if row.get("is_current")), None)
        if snapshot is None and snapshots:
            snapshot = max(snapshots, key=lambda row: int(row["snapshot_id"]))
        if snapshot is None:
            raise ValueError("没有可用的完整背包快照，请先完成背包同步。")
        profiles = dao.list_optimization_profiles()
        profile = next((row for row in profiles if row["name"] == _INTERNAL_PROFILE_NAME), None)
        if profile is None:
            profile = dao.create_optimization_profile(
                _INTERNAL_PROFILE_NAME, allocation_strategy="role_priority", characters=rows
            )
        else:
            version = profile["version"]
            if version.get("allocation_strategy") != "role_priority" or version.get("characters") != rows:
                dao.create_optimization_profile_version(
                    int(profile["profile_id"]), allocation_strategy="role_priority", characters=rows
                )
            profile = dao.get_optimization_profile(int(profile["profile_id"]))
        version = profile["version"]
        return int(snapshot["snapshot_id"]), int(profile["profile_id"]), int(version["version_number"])
