# 编辑正式弧盘身份及成长草稿，展示已证实的常驻属性与直伤投影。
from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.domain.official_role.advancement import (
    fork_active_panel_stats, fork_breakthrough_choices, fork_permanent_stats,
    select_fork_breakthrough,
)
from src.services.official_role.marginal_scoring import calculate_official_role_margins
from src.ui.widgets import NoWheelComboBox, NoWheelSpinBox
from ..fork_display import render_fork_skill_description
from ..role_calculation import (
    _attribute_name, _calculation_detail, _clear_layout, _mark_dirty,
    _refresh_role_calculations, _selected_fork_stage,
)
from .growth_fields import _display_property_value, _replace_breakthrough_choices

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
    # itemData 保留正式 fork_id；名称和专属外观后缀都不参与选择或保存身份。
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
        # 图鉴输入不升级为伤害证据；未证实口径继续显示待验证，绝不补公式。
        limited_catalog = detail.get("catalog_scope") in {"role_page", "reference"}
        current = None if limited_catalog else calculate_official_role_margins(with_fork, context_key)
        baseline = None if limited_catalog else calculate_official_role_margins(without_fork, context_key)
        if current and baseline and baseline["damage"] > 0:
            gain = (current["damage"] / baseline["damage"] - 1.0) * 100.0
            margin_label.setText(f"直伤收益: {gain:+.2f}%")
        else:
            margin_label.setText("直伤收益：待验证" if limited_catalog else "直伤收益: --")
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
