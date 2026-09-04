# 构建并渲染金色空幕主属性与弧盘固定轴综合收益表。
"""Qt presentation for selected-role equipment marginal benefits."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.domain.battle_counterfactual_quantification import QuantificationStatus
from src.domain.battle_marginal_benefit import (
    BattleForkMarginal,
    BattleMarginalBenefits,
    BattleMarginalDelta,
)
from src.features.battle_report.analysis_components import (
    analysis_section,
    analysis_table,
)
from src.features.battle_report.marginal_quantification_view import (
    quantification_status_text,
)


def build_marginal_benefit_sections(
    root: QVBoxLayout,
) -> tuple[QTableWidget, QLabel, QWidget, QTableWidget, QLabel]:
    core_card, core_layout = analysis_section("空幕主属性边际（金色候选）")
    core_note = QLabel(
        "识别当前任意品质空幕并固定套装与副词条，候选统一使用金色满级主属性；"
        "“相对无主属性”用于统一比较，“替换当前”用于实际换装决策。"
    )
    _style_note(core_note)
    core_layout.addWidget(core_note)
    core_notice = QLabel("等待后台计算…")
    _style_note(core_notice)
    core_layout.addWidget(core_notice)
    core_table = analysis_table(
        (
            "金色主属性",
            "主属性值",
            "相对无主属性·角色",
            "相对无主属性·全队",
            "替换当前·角色",
            "替换当前·全队",
            "候选角色伤害",
            "候选全队伤害",
            "量化状态",
        ),
        250,
        default_widths=(190, 110, 165, 165, 155, 155, 145, 145, 190),
    )
    core_layout.addWidget(core_table)
    root.addWidget(core_card)

    fork_panel = QWidget()
    fork_panel.setObjectName("battleForkBenefitPanel")
    fork_layout = QVBoxLayout(fork_panel)
    fork_layout.setContentsMargins(0, 8, 0, 0)
    fork_title = QLabel("固定轴综合收益")
    fork_title.setObjectName("battleForkBenefitTitle")
    fork_title.setStyleSheet(themed_style("font-weight:bold;color:#58a6ff"))
    fork_layout.addWidget(fork_title)
    fork_note = QLabel(
        "A=无弧盘，B=仅恢复弧盘常驻面板，C=完整弧盘。"
        "常驻=B-A，技能/机制=C-B，综合=C-A；团队 Buff 表仍作为机制明细，不与本表相加。"
    )
    _style_note(fork_note)
    fork_layout.addWidget(fork_note)
    fork_notice = QLabel("等待后台计算…")
    _style_note(fork_notice)
    fork_layout.addWidget(fork_notice)
    fork_table = analysis_table(
        (
            "无弧盘角色伤害",
            "无弧盘全队伤害",
            "常驻·角色",
            "常驻·全队",
            "技能/机制·角色",
            "技能/机制·全队",
            "综合·角色",
            "综合·全队",
            "量化状态",
        ),
        135,
        default_widths=(150, 150, 145, 145, 165, 165, 145, 145, 205),
    )
    fork_table.setFixedHeight(88)
    fork_layout.addWidget(fork_table)
    return core_table, core_notice, fork_panel, fork_table, fork_notice


def render_marginal_benefits(
    core_table: QTableWidget,
    core_notice: QLabel,
    fork_table: QTableWidget,
    fork_notice: QLabel,
    benefits: BattleMarginalBenefits | None,
    *,
    character_id: int | None,
) -> None:
    if benefits is None or benefits.character_id != character_id:
        core_table.setRowCount(0)
        fork_table.setRowCount(0)
        core_notice.setText("等待所选角色的后台固定轴计算…")
        fork_notice.setText("等待所选角色的后台固定轴计算…")
        core_notice.show()
        fork_notice.show()
        return
    _render_core(core_table, core_notice, benefits)
    _render_fork(fork_table, fork_notice, benefits.fork)


def _render_core(
    table: QTableWidget,
    notice: QLabel,
    benefits: BattleMarginalBenefits,
) -> None:
    rows = benefits.core_main_stats
    table.setRowCount(len(rows))
    notice.setText(benefits.core_notice)
    notice.setVisible(bool(benefits.core_notice))
    for row_index, row in enumerate(rows):
        values = (
            f"{row.label}{'（当前同类）' if row.is_current else ''}",
            _property_value(row.value, row.is_percent),
            _gain(row.contribution.role_status, row.contribution.role_gain_percent),
            _gain(row.contribution.team_status, row.contribution.team_gain_percent),
            _gain(row.replacement.role_status, row.replacement.role_gain_percent),
            _gain(row.replacement.team_status, row.replacement.team_gain_percent),
            _damage(row.contribution.projected_role_damage),
            _damage(row.contribution.projected_team_damage),
            _status(row.contribution),
        )
        tooltip = _delta_tooltip("相对无主属性", row.contribution)
        tooltip += "\n" + _delta_tooltip("替换当前主属性", row.replacement)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(tooltip)
            table.setItem(row_index, column, item)


def _render_fork(
    table: QTableWidget,
    notice: QLabel,
    fork: BattleForkMarginal | None,
) -> None:
    if fork is None:
        table.setRowCount(0)
        notice.setText("当前角色缺少弧盘分析结果。")
        notice.show()
        return
    if fork.unavailable_reason:
        table.setRowCount(0)
        notice.setText(fork.unavailable_reason)
        notice.show()
        return
    notice.hide()
    assert fork.permanent is not None
    assert fork.skill is not None
    assert fork.comprehensive is not None
    table.setRowCount(1)
    values = (
        _damage(fork.no_fork_role_damage),
        _damage(fork.no_fork_team_damage),
        _gain(fork.permanent.role_status, fork.permanent.role_gain_percent),
        _gain(fork.permanent.team_status, fork.permanent.team_gain_percent),
        _gain(fork.skill.role_status, fork.skill.role_gain_percent),
        _gain(fork.skill.team_status, fork.skill.team_gain_percent),
        _gain(
            fork.comprehensive.role_status,
            fork.comprehensive.role_gain_percent,
        ),
        _gain(
            fork.comprehensive.team_status,
            fork.comprehensive.team_gain_percent,
        ),
        _status(fork.comprehensive),
    )
    tooltip = "\n".join((
        _delta_tooltip("弧盘常驻", fork.permanent),
        _delta_tooltip("弧盘技能/机制", fork.skill),
        _delta_tooltip("弧盘综合", fork.comprehensive),
        _closure_text(fork),
    ))
    for column, value in enumerate(values):
        item = QTableWidgetItem(value)
        item.setToolTip(tooltip)
        table.setItem(0, column, item)


def _style_note(label: QLabel) -> None:
    label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    label.setWordWrap(True)


def _property_value(value: float, percent: bool) -> str:
    return f"{value * 100:.2f}%" if percent else f"{value:,.2f}"


def _damage(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def _gain(status: QuantificationStatus, value: float | None) -> str:
    if status == "unavailable" or value is None:
        return "—"
    if status == "not_applicable":
        return "+0.00%"
    text = f"{value:+.2f}%"
    return f"{text}（部分）" if status == "partial" else text


def _status(delta: BattleMarginalDelta) -> str:
    return (
        f"角色{quantification_status_text(delta.role_status)} "
        f"{delta.role_coverage_percent:.1f}% / "
        f"全队{quantification_status_text(delta.team_status)} "
        f"{delta.team_coverage_percent:.1f}%"
    )


def _delta_tooltip(label: str, delta: BattleMarginalDelta) -> str:
    gaps = "\n".join(f"- {line}" for line in delta.gap_explanations)
    text = f"{label}：{_status(delta)}。"
    return text if not gaps else f"{text}\n缺失依赖：\n{gaps}"


def _closure_text(fork: BattleForkMarginal) -> str:
    if fork.closure_role_damage is None or fork.closure_team_damage is None:
        return "A/B/C 闭合：当前量化状态不足，未给出精确闭合差。"
    return (
        "A/B/C 闭合差："
        f"角色 {fork.closure_role_damage:+,.2f}，"
        f"全队 {fork.closure_team_damage:+,.2f}。"
    )


__all__ = ["build_marginal_benefit_sections", "render_marginal_benefits"]
