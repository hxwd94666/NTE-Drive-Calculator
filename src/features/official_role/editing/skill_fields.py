# 编辑正式 SkillID 的基础等级，独立展示觉醒后的生效等级与倍率详情。
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QDialog, QGroupBox, QHeaderView, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.services.damage_calculation_service import skill_tier_for_effective_level
from src.services.official_role.awakening import awaken_skill_level_delta
from src.ui.widgets import NoWheelSpinBox
from ..role_calculation import _mark_dirty, _refresh_role_calculations

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
    # 弹窗归调用页面所有，复用屏幕限位；关闭详情不提交任何角色草稿。
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

    # 字典键是正式 SkillID，值只存基础等级；觉醒加级只用于派生展示，避免重复保存。
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
