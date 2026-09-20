# 构建只读取官方静态库与账号 SQLite 指针的新角色页面。
"""Rebuilt character page using the old UI skeleton and official data sources."""

from __future__ import annotations


from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.services.advancement_stage_service import (
    character_growth_choices,
    fork_breakthrough_choices,
    fork_active_panel_stats,
    fork_permanent_stats,
    select_character_growth,
    select_fork_breakthrough,
)
from src.services.damage_calculation_service import skill_tier_for_effective_level
from src.services.official_role_awakening_service import (
    awaken_skill_level_delta,
)
from src.services.world_bonus_settings_service import world_bonus_property_stats
from src.services.official_role_page_service import (
    calculate_official_role_margins,
)
from src.ui.widgets import (
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
)
from .role_calculation import (
    _attribute_name,
    _calculation_detail,
    _clear_layout,
    _mark_dirty,
    _refresh_role_calculations,
    _selected_fork_stage,
    _selected_growth,
)
from .fork_display import render_fork_skill_description

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

def _replace_breakthrough_choices(
    combo: NoWheelComboBox,
    label_widget: QLabel,
    choices,
    selected,
    *, stage_key: str,
) -> None:
    combo.blockSignals(True)
    combo.clear()
    for index, row in enumerate(choices):
        combo.addItem(
            "突破前" if index == 0 else "突破后",
            int(row.get(stage_key) or 0),
        )
    if selected is not None:
        selected_index = combo.findData(int(selected.get(stage_key) or 0))
        combo.setCurrentIndex(max(0, selected_index))
    combo.blockSignals(False)
    ambiguous = len(choices) > 1
    label_widget.setVisible(ambiguous)
    combo.setVisible(ambiguous)


def _build_base_group(window, character_id: int, detail: dict, editor: dict) -> QGroupBox:
    character = detail["character"]
    profile = detail["profile"]
    growth_rows = detail["growth_rows"]
    group = QGroupBox("基础加成")
    group.setObjectName("officialRoleBaseGroup")
    group.setStyleSheet("QGroupBox{font-weight:bold;}")
    layout = QVBoxLayout(group)
    content = QHBoxLayout()
    content.setSpacing(16)

    left = QWidget()
    left.setMinimumWidth(132)
    left.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    left_layout = QVBoxLayout(left)
    left_layout.setContentsMargins(0, 0, 0, 0)
    left_layout.setSpacing(8)
    left_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
    icon_path = detail.get("icon_path")
    if icon_path:
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            avatar = QLabel()
            avatar.setObjectName("officialRoleBaseAvatar")
            avatar.setFixedSize(96, 96)
            avatar.setScaledContents(True)
            avatar.setPixmap(pixmap)
            left_layout.addWidget(avatar, alignment=Qt.AlignHCenter)
    role_name = QLabel(str(character.get("name_zh") or character_id))
    role_name.setAlignment(Qt.AlignHCenter)
    role_name.setStyleSheet("font-weight:bold;color:#58a6ff;")
    left_layout.addWidget(role_name)
    growth_combo = NoWheelSpinBox()
    growth_combo.setRange(
        min(int(row["level"]) for row in growth_rows),
        max(int(row["level"]) for row in growth_rows),
    )
    growth_combo.setValue(int(profile["character_level"]))
    growth_combo.setButtonSymbols(QSpinBox.NoButtons)
    level_row = QHBoxLayout()
    level_row.setSpacing(6)
    level_label = QLabel("等级:")
    level_label.setStyleSheet("font-weight:bold;color:#58a6ff;")
    level_row.addWidget(level_label)
    growth_combo.setFixedWidth(72)
    level_row.addWidget(growth_combo)
    left_layout.addLayout(level_row)
    breakthrough_row = QHBoxLayout()
    breakthrough_row.setSpacing(6)
    breakthrough_label = QLabel("突破:")
    breakthrough_label.setStyleSheet("font-weight:bold;color:#58a6ff;")
    breakthrough_combo = NoWheelComboBox()
    breakthrough_combo.setFixedWidth(96)
    breakthrough_row.addWidget(breakthrough_label)
    breakthrough_row.addWidget(breakthrough_combo)
    left_layout.addLayout(breakthrough_row)
    left_layout.addStretch()
    content.addWidget(left)

    right = QWidget()
    right_layout = QVBoxLayout(right)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(8)
    likeability_bonus = detail.get("likeability_bonus") or {}
    likeability_properties = {
        str(row.get("property_id") or ""): float(row.get("value") or 0.0)
        for row in likeability_bonus.get("properties") or ()
        if str(row.get("property_id") or "")
    }
    likeability_parts = [
        f"{_attribute_name(detail, property_id)} {_display_property_value(detail, property_id, value)}"
        for property_id, value in likeability_properties.items()
    ]
    likeability = QCheckBox(
        "好感度 10 级" + (f"（{'、'.join(likeability_parts)}）" if likeability_parts else "")
    )
    likeability.setChecked(
        bool(profile.get("likeability_level_10_enabled"))
        and bool(likeability_properties)
    )
    likeability.setEnabled(bool(likeability_properties))
    if not likeability_properties:
        if str((detail.get("character") or {}).get("logical_character_key")) == "protagonist":
            likeability.setText("玩家角色无好感度系统")
            likeability.setToolTip("零是玩家自身，不提供好感度等级与十级属性加成。")
        else:
            likeability.setToolTip("当前静态资料未提供该角色的好感度 10 级属性。")
    right_layout.addWidget(likeability)

    stats_grid = QGridLayout()
    stats_grid.setHorizontalSpacing(14)
    stats_grid.setVerticalSpacing(8)
    stat_values = {}
    stat_specs = (
        ("角色生命值", "hp_base"),
        ("角色攻击力", "atk_base"),
        ("角色防御力", "def_base"),
        ("暴击率%", "crit_rate"),
        ("暴击伤害%", "crit_damage"),
    )
    for stat_index, (label_text, key) in enumerate(stat_specs):
        grid_row = stat_index // 2
        grid_column = (stat_index % 2) * 2
        label = QLabel(label_text)
        label.setMinimumWidth(92)
        spin = NoWheelDoubleSpinBox()
        spin.setRange(-999999, 999999)
        spin.setDecimals(2)
        spin.setReadOnly(True)
        spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        spin.setMinimumWidth(110)
        stat_values[key] = spin
        stats_grid.addWidget(label, grid_row, grid_column)
        stats_grid.addWidget(spin, grid_row, grid_column + 1)
    stats_grid.setColumnStretch(1, 1)
    stats_grid.setColumnStretch(3, 1)
    right_layout.addLayout(stats_grid)
    content.addWidget(right, 1)
    layout.addLayout(content)

    def sync_breakthrough_choices(preferred_stage: int | None = None) -> None:
        level = int(growth_combo.value())
        if preferred_stage is None and breakthrough_combo.count():
            current_stage = breakthrough_combo.currentData()
            preferred_stage = (
                int(current_stage) if current_stage is not None else None
            )
        choices = character_growth_choices(growth_rows, level)
        selected = select_character_growth(
            growth_rows,
            level,
            preferred_stage=preferred_stage,
        )
        _replace_breakthrough_choices(
            breakthrough_combo,
            breakthrough_label,
            choices,
            selected,
            stage_key="breakthrough_stage",
        )

    editor.update(
        growth=growth_combo, growth_rows=growth_rows,
        growth_breakthrough=breakthrough_combo,
        likeability_level_10=likeability,
    )
    sync_breakthrough_choices(int(profile["breakthrough_stage"]))

    def update_stats() -> None:
        growth = _selected_growth(editor)
        selected = next(
            (
                row
                for row in growth_rows
                if growth is not None
                and (
                    int(row.get("level") or 0),
                    int(row.get("breakthrough_stage") or 0),
                )
                == growth
            ),
            {},
        )
        bonus = world_bonus_property_stats(detail.get("world_bonus"))
        if likeability.isChecked():
            for property_id, value in likeability_properties.items():
                bonus[property_id] = bonus.get(property_id, 0.0) + value
        hp_base = float(selected.get("hp_base") or 0)
        atk_base = float(selected.get("atk_base") or 0)
        def_base = float(selected.get("def_base") or 0)
        stat_values["hp_base"].setValue(
            hp_base * (1.0 + bonus.get("HPMaxUp", 0.0)) + bonus.get("HPMaxAdd", 0.0)
        )
        stat_values["atk_base"].setValue(
            atk_base * (1.0 + bonus.get("AtkUp", 0.0)) + bonus.get("AtkAdd", 0.0)
        )
        stat_values["def_base"].setValue(
            def_base * (1.0 + bonus.get("DefUp", 0.0)) + bonus.get("DefAdd", 0.0)
        )
        stat_values["crit_rate"].setValue(5.0 + bonus.get("CritBase", 0.0) * 100.0)
        stat_values["crit_damage"].setValue(
            50.0 + bonus.get("CritDamageBase", 0.0) * 100.0
        )

    update_stats()
    growth_combo.valueChanged.connect(lambda _value: sync_breakthrough_choices())
    growth_combo.valueChanged.connect(update_stats)
    breakthrough_combo.currentIndexChanged.connect(update_stats)

    def mark_and_refresh(*_args) -> None:
        _mark_dirty(window, character_id)
        _refresh_role_calculations(editor)

    growth_combo.valueChanged.connect(mark_and_refresh)
    breakthrough_combo.currentIndexChanged.connect(mark_and_refresh)
    likeability.toggled.connect(update_stats)
    likeability.toggled.connect(mark_and_refresh)
    return group


def _skill_name(skill: dict) -> str:
    return str(
        skill.get("display_name_zh")
        or skill.get("skill_id")
        or "技能"
    )


def _damage_multiplier_row(
    damage: dict,
    effective_level: int,
) -> tuple[str, str, str, str] | None:
    choices = (
        ("攻击力", damage.get("atk_rate_base") or ()),
        ("生命值", damage.get("hp_rate_base") or ()),
        ("防御力", damage.get("def_rate_base") or ()),
    )
    scaling_name, values = next(
        ((name, values) for name, values in choices if values),
        ("", ()),
    )
    if not values:
        return None
    tier = min(skill_tier_for_effective_level(effective_level), len(values) - 1)
    multiplier = float(values[tier])
    return (
        str(damage.get("display_name_zh") or damage.get("damage_id") or "倍率项"),
        scaling_name,
        str(damage.get("damage_type_name_zh") or damage.get("damage_type") or ""),
        f"{multiplier * 100:.2f}%".replace(".00%", "%"),
    )


def _show_skill_detail(
    window,
    detail: dict,
    editor: dict,
    skill: dict,
) -> None:
    skill_id = str(skill.get("skill_id") or "")
    base_level = int((editor.get("skill_levels") or {}).get(skill_id, 1))
    profile = {
        **detail["profile"],
        "awakening_level": editor["awakening_level"].value(),
        "selected_awaken_effect_ids": [
            effect_id
            for effect_id, check in (editor.get("awakening_checks") or {}).items()
            if check.isChecked()
        ],
        "awakening_selection_initialized": True,
    }
    delta = awaken_skill_level_delta(profile, detail.get("awakenings") or (), skill_id)
    effective_level = base_level + delta
    rows = []
    for damage in skill.get("damage_entries") or ():
        row = _damage_multiplier_row(damage, effective_level)
        if row is not None:
            rows.append((damage, row))
    dialog = QDialog(window)
    dialog.setWindowTitle(f"{_skill_name(skill)} - 技能倍率详情")
    dialog_layout = QVBoxLayout(dialog)
    summary = QLabel(
        f"基础等级 {base_level}"
        + (f" + 觉醒 {delta} = 生效等级 {effective_level}" if delta else "")
    )
    summary.setStyleSheet("font-weight:bold;color:#58a6ff;")
    dialog_layout.addWidget(summary)
    table = QTableWidget(len(rows), 4)
    table.setHorizontalHeaderLabels(("倍率项", "倍率属性", "伤害类型", "当前倍率"))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    for row_index, (damage, values) in enumerate(rows):
        for column_index, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column_index == 0:
                item.setToolTip(str(damage.get("damage_id") or ""))
            elif column_index == 2:
                item.setToolTip(str(damage.get("damage_type") or ""))
            table.setItem(row_index, column_index, item)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    for column in range(1, 4):
        table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
    dialog_layout.addWidget(table)
    if not rows:
        dialog_layout.addWidget(QLabel("当前静态资料没有可展示的技能倍率。"))
    close = QPushButton("关闭")
    close.clicked.connect(dialog.accept)
    dialog_layout.addWidget(close)
    fit_dialog_to_available_screen(dialog, QSize(820, 560))
    dialog.exec()


def _build_skill_group(
    window,
    character_id: int,
    detail: dict,
    editor: dict,
) -> QGroupBox:
    profile = detail["profile"]
    group = QGroupBox("技能")
    group.setObjectName("officialRoleSkillGroup")
    layout = QVBoxLayout(group)

    skill_levels = {
        str(key): int(value)
        for key, value in (profile.get("skill_levels") or {}).items()
    }
    effective_labels: dict[str, QLabel] = {}
    for skill in detail.get("skills") or ():
        skill_id = str(skill.get("skill_id") or "")
        levels = [int(row.get("level") or 0) for row in skill.get("levels") or ()]
        maximum = max(levels, default=0) + 1
        skill_levels.setdefault(skill_id, maximum)
        row = QHBoxLayout()
        name = QLabel(_skill_name(skill))
        name.setMinimumWidth(64)
        row.addWidget(name)
        row.addWidget(QLabel("等级:"))
        level = NoWheelSpinBox()
        level.setRange(1, maximum)
        level.setValue(skill_levels[skill_id])
        level.setFixedWidth(72)
        row.addWidget(level)
        effective = QLabel()
        effective.setStyleSheet("color:#8b949e;")
        effective_labels[skill_id] = effective
        row.addWidget(effective)
        row.addStretch()
        detail_button = QPushButton("详情")
        detail_button.clicked.connect(
            lambda _checked=False, target=skill: _show_skill_detail(
                window, detail, editor, target
            )
        )
        row.addWidget(detail_button)
        layout.addLayout(row)

        def level_changed(value: int, target_id: str = skill_id) -> None:
            skill_levels[target_id] = int(value)
            refresh_effective_levels()
            refresh_awakening = editor.get("refresh_awakening_descriptions")
            if refresh_awakening is not None:
                refresh_awakening()
            _mark_dirty(window, character_id)
            _refresh_role_calculations(editor)

        level.valueChanged.connect(level_changed)

    editor.update({
        "skill_levels": skill_levels,
    })

    def refresh_effective_levels() -> None:
        selected = [
            effect_id
            for effect_id, check in (editor.get("awakening_checks") or {}).items()
            if check.isChecked()
        ]
        calculation_profile = {
            **profile,
            "awakening_level": editor["awakening_level"].value(),
            "selected_awaken_effect_ids": selected,
            "awakening_selection_initialized": True,
        }
        for skill in detail.get("skills") or ():
            skill_id = str(skill.get("skill_id") or "")
            delta = awaken_skill_level_delta(
                calculation_profile,
                detail.get("awakenings") or (),
                skill_id,
            )
            effective_level = skill_levels[skill_id] + delta
            effective_labels[skill_id].setText(
                f"生效 {effective_level} 级（3觉 +1）"
                if delta
                else f"生效 {effective_level} 级"
            )

    editor.setdefault("awakening_refreshers", []).append(refresh_effective_levels)
    refresh_effective_levels()
    refresh_awakening = editor.get("refresh_awakening_descriptions")
    if refresh_awakening is not None:
        refresh_awakening()

    return group


def _display_property_value(detail: dict, property_id: str, value: float) -> str:
    attribute = detail.get("attributes", {}).get(property_id, {})
    if attribute.get("show_percent"):
        return f"+{value * 100:.2f}%".replace(".00%", "%")
    return f"+{value:.2f}".rstrip("0").rstrip(".")


def _build_fork_group(
    window,
    character_id: int,
    detail: dict,
    editor: dict,
    *,
    show_direct_damage_margin: bool = True,
) -> QGroupBox:
    profile = detail["profile"]
    group = QGroupBox("弧盘加成")
    group.setObjectName("officialRoleForkGroup")
    layout = QVBoxLayout(group)
    identity = QHBoxLayout()
    identity.addWidget(QLabel("名称:"))
    fork_combo = NoWheelComboBox()
    fork_combo.setMaxVisibleItems(10)
    fork_combo.addItem("未装备弧盘", None)
    for fork in detail["forks"]:
        exclusive = str(character_id) in {str(value) for value in fork.get("exclusive_character_ids") or []}
        suffix = "（专属外观）" if exclusive else "（同类型）"
        fork_combo.addItem(f"{fork.get('name_zh') or fork['fork_id']} {suffix}", fork["fork_id"])
    fork_index = fork_combo.findData(profile.get("fork_id"))
    fork_combo.setCurrentIndex(fork_index if fork_index >= 0 else 0)
    identity.addWidget(fork_combo, 1)
    fork_level = NoWheelSpinBox()
    fork_level.setRange(1, 80)
    fork_level.setValue(int(profile.get("fork_level") or 80))
    identity.addWidget(QLabel("等级:"))
    identity.addWidget(fork_level)
    fork_breakthrough_label = QLabel("突破:")
    fork_breakthrough = NoWheelComboBox()
    fork_breakthrough.setFixedWidth(96)
    identity.addWidget(fork_breakthrough_label)
    identity.addWidget(fork_breakthrough)
    refinement = NoWheelComboBox()
    refinement.setMaxVisibleItems(5)
    for level in range(1, 6):
        refinement.addItem(str(level), level)
    refinement_index = refinement.findData(int(profile.get("fork_refinement_level") or 1))
    refinement.setCurrentIndex(refinement_index if refinement_index >= 0 else 0)
    identity.addWidget(QLabel("精炼:"))
    identity.addWidget(refinement)
    margin_label = QLabel("直伤收益: --")
    margin_label.setStyleSheet("color:#ffaa00;font-weight:bold;font-size:13px;")
    identity.addWidget(margin_label)
    layout.addLayout(identity)
    # 先挂入 group，避免可见标签短暂成为顶层窗口。
    margin_label.setVisible(show_direct_damage_margin)
    base_label = QLabel("基础加成（橙色为精炼无条件常驻）：")
    base_label.setStyleSheet("font-weight:bold;color:#58a6ff;")
    layout.addWidget(base_label)
    stats_widget = QWidget()
    stats_layout = QVBoxLayout(stats_widget)
    stats_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(stats_widget)
    effect_label = QLabel("技能描述：")
    effect_label.setStyleSheet("font-weight:bold;color:#58a6ff;")
    layout.addWidget(effect_label)
    effect_text = QLabel()
    effect_text.setWordWrap(True)
    effect_text.setMinimumHeight(72)
    layout.addWidget(effect_text)

    def selected_fork_template():
        fork_id = fork_combo.currentData()
        return next(
            (item for item in detail["forks"] if item.get("fork_id") == fork_id),
            None,
        )

    def sync_fork_breakthrough_choices(preferred_stage: int | None = None) -> None:
        template = selected_fork_template()
        level = int(fork_level.value())
        if preferred_stage is None and fork_breakthrough.count():
            current_stage = fork_breakthrough.currentData()
            preferred_stage = (
                int(current_stage) if current_stage is not None else None
            )
        choices = fork_breakthrough_choices(
            (template or {}).get("breakthroughs") or (),
            level,
        )
        selected = select_fork_breakthrough(
            (template or {}).get("breakthroughs") or (),
            level,
            preferred_stage=preferred_stage,
        )
        _replace_breakthrough_choices(
            fork_breakthrough,
            fork_breakthrough_label,
            choices,
            selected,
            stage_key="stage",
        )

    editor.update(
        fork=fork_combo, fork_level=fork_level,
        fork_breakthrough=fork_breakthrough, refinement=refinement,
    )
    sync_fork_breakthrough_choices(profile.get("fork_breakthrough_stage"))

    def refresh_fork_summary() -> None:
        _clear_layout(stats_layout)
        fork_id = fork_combo.currentData()
        level = fork_level.value()
        breakthrough_stage = _selected_fork_stage(editor)
        fork = selected_fork_template()
        permanent_stats = fork_permanent_stats(fork, refinement.currentData())
        stats = fork_active_panel_stats(fork, level,
            breakthrough_stage=breakthrough_stage,
            refinement_level=refinement.currentData(),
        )
        if not stats:
            stats_layout.addWidget(QLabel("未装备弧盘"))
        for property_id, value in stats.items():
            row = QHBoxLayout()
            property_label = _attribute_name(detail, property_id)
            permanent_value = permanent_stats.get(property_id)
            if permanent_value is not None:
                property_label += f"（常驻 {_display_property_value(detail, property_id, permanent_value)}）"
            row.addWidget(QLabel(property_label))
            row.addStretch()
            shown = QLabel(_display_property_value(detail, property_id, value))
            shown.setStyleSheet(
                "color:#ffb75d;font-weight:700;" if permanent_value is not None
                else "color:#58a6ff;font-weight:700;"
            )
            row.addWidget(shown)
            stats_layout.addLayout(row)
        context_key = str(editor.get("equipment_context_key") or "current")
        calculation_detail = _calculation_detail(detail, editor)
        with_fork = {
            **calculation_detail,
            "profile": {
                **calculation_detail["profile"],
                "fork_id": fork_id,
                "fork_level": level,
                "fork_breakthrough_stage": breakthrough_stage,
                "fork_refinement_level": refinement.currentData(),
            },
        }
        without_fork = {
            **calculation_detail,
            "profile": {**calculation_detail["profile"], "fork_id": None},
        }
        current = calculate_official_role_margins(with_fork, context_key)
        baseline = calculate_official_role_margins(without_fork, context_key)
        if current and baseline and baseline["damage"] > 0:
            gain = (current["damage"] / baseline["damage"] - 1.0) * 100.0
            margin_label.setText(f"直伤收益: {gain:+.2f}%")
        else:
            margin_label.setText("直伤收益: --")
        star_rows = list((fork or {}).get("star_levels") or ())
        star = next(
            (row for row in star_rows if int(row.get("star_level") or 0) == refinement.currentData()),
            star_rows[0] if star_rows else None,
        )
        if star:
            description = render_fork_skill_description(star)
            effect_text.setText(f"{star.get('title_zh') or ''}\n{description}".strip())
        else:
            effect_text.setText("暂无官方精炼说明。")

    fork_combo.currentIndexChanged.connect(lambda _index: sync_fork_breakthrough_choices())
    fork_level.valueChanged.connect(lambda _value: sync_fork_breakthrough_choices())
    fork_combo.currentIndexChanged.connect(refresh_fork_summary)
    fork_level.valueChanged.connect(refresh_fork_summary)
    fork_breakthrough.currentIndexChanged.connect(refresh_fork_summary)
    refinement.currentIndexChanged.connect(refresh_fork_summary)
    refresh_fork_summary()

    def mark_and_refresh(*_args) -> None:
        _mark_dirty(window, character_id)
        _refresh_role_calculations(editor)

    for widget in (fork_combo, fork_level, fork_breakthrough, refinement):
        signal = getattr(widget, "currentIndexChanged", None) or widget.valueChanged
        signal.connect(mark_and_refresh)
    return group
