# 编辑官方角色成长与好感草稿，并投影基础属性及突破选择。
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from src.domain.official_role.advancement import (
    character_growth_choices, select_character_growth,
)
from src.domain.official_role.models import world_bonus_property_stats
from src.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox
from ..role_calculation import (
    _attribute_name, _mark_dirty, _refresh_role_calculations, _selected_growth,
)

# 两类成长选择共用正式 stage 值；按钮文字不承担突破身份。
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


def _display_property_value(detail: dict, property_id: str, value: float) -> str:
    # 静态比例仍为小数，仅在标签中乘 100；不把展示百分数回写计算输入。
    attribute = detail.get("attributes", {}).get(property_id, {})
    if attribute.get("show_percent"):
        return f"+{value * 100:.2f}%".replace(".00%", "%")
    return f"+{value:.2f}".rstrip("0").rstrip(".")


def _build_base_group(window, character_id: int, detail: dict, editor: dict) -> QGroupBox:
    # detail 已由读取 owner 物化；本组件只改草稿，保存和取消仍归调用页面。
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
    # 中文名只作展示；脏草稿始终以传入的正式 character_id 标记。
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
        # 本区是成长/家具/好感的基础展示，不冒充含弧盘和觉醒的完整有效面板。
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
