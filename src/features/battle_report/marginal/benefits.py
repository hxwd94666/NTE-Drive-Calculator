# 构建并渲染金色空幕主属性与弧盘固定轴综合收益表。
"""Qt presentation for selected-role equipment marginal benefits."""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import plain_label, resolve_text_catalog
from PySide6.QtWidgets import QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.domain.battle_report.counterfactual.quantification import QuantificationStatus
from src.domain.battle_report.counterfactual.results import (
    BattleForkMarginal, BattleMarginalBenefits, BattleMarginalDelta,
)
from src.features.battle_report.analysis.components import (
    analysis_section,
    analysis_table,
)
from src.features.battle_report.analysis.quantification import (
    quantification_status_text,
)


def build_marginal_benefit_sections(
    root: QVBoxLayout,
    *, text_catalog=None,
) -> tuple[QTableWidget, QLabel, QWidget, QTableWidget, QLabel]:
    core_card, core_layout = analysis_section(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_card_core_layout'))
    core_note = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_note'))
    _style_note(core_note)
    core_layout.addWidget(core_note)
    core_notice = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_notice'))
    _style_note(core_notice)
    core_layout.addWidget(core_notice)
    core_table = analysis_table(
        (
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_table'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_table_2'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_table_3'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_table_4'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_table_5'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_table_6'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_table_7'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_table_8'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_table_9'),
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
    fork_title = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.fork_title'))
    fork_title.setObjectName("battleForkBenefitTitle")
    fork_title.setStyleSheet(themed_style("font-weight:bold;color:#58a6ff"))
    fork_layout.addWidget(fork_title)
    fork_note = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.fork_note'))
    _style_note(fork_note)
    fork_layout.addWidget(fork_note)
    fork_notice = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_notice'))
    _style_note(fork_notice)
    fork_layout.addWidget(fork_notice)
    fork_table = analysis_table(
        (
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.fork_table'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.fork_table_2'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.fork_table_3'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.fork_table_4'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.fork_table_5'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.fork_table_6'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.fork_table_7'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.fork_table_8'),
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_benefit_sections.core_table_9'),
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
    text_catalog=None,
) -> None:
    # 所选角色没有结果时清空旧表，不把另一角色的已算结果当作本次结果。
    if benefits is None or benefits.character_id != character_id:
        core_table.setRowCount(0)
        fork_table.setRowCount(0)
        core_notice.setText(resolve_text_catalog(text_catalog).render('ui.battle_report.render_marginal_benefits.core_notice'))
        fork_notice.setText(resolve_text_catalog(text_catalog).render('ui.battle_report.render_marginal_benefits.core_notice'))
        core_notice.show()
        fork_notice.show()
        return
    _render_core(core_table, core_notice, benefits, text_catalog=text_catalog)
    _render_fork(fork_table, fork_notice, benefits.fork, text_catalog=text_catalog)


def _render_core(
    table: QTableWidget,
    notice: QLabel,
    benefits: BattleMarginalBenefits,
    *, text_catalog=None,
) -> None:
    rows = sorted(
        benefits.core_main_stats,
        key=lambda row: (
            row.contribution.role_gain_percent is not None,
            row.contribution.role_gain_percent or 0.0,
        ),
        reverse=True,
    )
    table.setRowCount(len(rows))
    notice.setText(benefits.core_notice)
    notice.setVisible(bool(benefits.core_notice))
    for row_index, row in enumerate(rows):
        values = (
            f"{row.label}{ui_enum_text(text_catalog, '（当前同类）') if row.is_current else ''}",
            _property_value(row.value, row.is_percent),
            _gain(row.contribution.role_status, row.contribution.role_gain_percent),
            _gain(row.contribution.team_status, row.contribution.team_gain_percent),
            _gain(row.replacement.role_status, row.replacement.role_gain_percent),
            _gain(row.replacement.team_status, row.replacement.team_gain_percent),
            _damage(row.contribution.projected_role_damage),
            _damage(row.contribution.projected_team_damage),
            _status(row.contribution, text_catalog=text_catalog),
        )
        tooltip = _delta_tooltip("相对无主属性", row.contribution, text_catalog=text_catalog)
        tooltip += "\n" + _delta_tooltip("替换当前主属性", row.replacement, text_catalog=text_catalog)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(tooltip)
            table.setItem(row_index, column, item)


def _render_fork(
    table: QTableWidget,
    notice: QLabel,
    fork: BattleForkMarginal | None,
    *, text_catalog=None,
) -> None:
    if fork is None:
        table.setRowCount(0)
        notice.setText(resolve_text_catalog(text_catalog).render('ui.battle_report.render_fork.notice'))
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
        _status(fork.comprehensive, text_catalog=text_catalog),
    )
    tooltip = "\n".join((
        _delta_tooltip("弧盘常驻", fork.permanent, text_catalog=text_catalog),
        _delta_tooltip("弧盘技能/机制", fork.skill, text_catalog=text_catalog),
        _delta_tooltip("弧盘综合", fork.comprehensive, text_catalog=text_catalog),
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
    # 未量化/缺值用横线；明确不适用的零收益与资料未知分开，部分结果的缺口留在状态提示。
    if status == "unavailable" or value is None:
        return "—"
    if status == "not_applicable":
        return "+0.00%"
    return f"{value:+.2f}%"


def _status(delta: BattleMarginalDelta, *, details: bool = False, text_catalog=None) -> str:
    role = quantification_status_text(delta.role_status) if details or delta.role_status != "partial" else ""
    team = quantification_status_text(delta.team_status) if details or delta.team_status != "partial" else ""
    return localized_text(
        text_catalog, "battle_report.marginal.status.coverage", "角色{role} {role_percent}% / 全队{team} {team_percent}%",
        role=role, role_percent=f"{delta.role_coverage_percent:.1f}", team=team,
        team_percent=f"{delta.team_coverage_percent:.1f}")


def _delta_tooltip(label: str, delta: BattleMarginalDelta, *, text_catalog=None) -> str:
    gaps = "\n".join(f"- {line}" for line in delta.gap_explanations)
    text = f"{ui_enum_text(text_catalog, label)}：{_status(delta, details=True, text_catalog=text_catalog)}。"
    return text if not gaps else localized_text(text_catalog, 'auto.features.battle_report.marginal.benefits.delta_tooltip.f715c3194', '{text}\n缺失依赖：\n{gaps}', text=f'{text}', gaps=f'{gaps}')


def _closure_text(fork: BattleForkMarginal, *, text_catalog=None) -> str:
    if fork.closure_role_damage is None or fork.closure_team_damage is None:
        return "A/B/C 闭合：当前量化状态不足，未给出精确闭合差。"
    return (
        localized_text(text_catalog, 'auto.features.battle_report.marginal.benefits.closure_text.f6ff50046', 'A/B/C 闭合差：角色 {closure_role_damage}，全队 {closure_team_damage}。', closure_role_damage=f'{fork.closure_role_damage:+,.2f}', closure_team_damage=f'{fork.closure_team_damage:+,.2f}')
    )


__all__ = ["build_marginal_benefit_sections", "render_marginal_benefits"]
