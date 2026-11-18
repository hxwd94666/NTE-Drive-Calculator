# 渲染固定轴属性与 Buff 结果；有预估时展示数值，依据保留在提示中。
"""Table renderers for fixed-axis marginal results."""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
from collections.abc import Sequence

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from src.domain.battle_report.counterfactual.results import BattleBuffCounterfactualResult
from src.domain.battle_report.counterfactual.results import BattleMarginalResult
from src.domain.battle_report.counterfactual.quantification import QuantificationStatus
from src.features.battle_report.analysis.quantification import (
    damage_coverage_text,
    quantification_status_text,
    quantified_coverage_text,
)


BUFF_BENEFIT_HEADERS = (
    "来源角色",
    "Buff / 被动",
    "受益角色",
    "获得伤害",
    "受益角色提升",
    "折合全队贡献",
    "Buff 全队增伤",
    "角色伤害覆盖",
    "团队伤害覆盖",
)
BUFF_BENEFIT_WIDTHS = (
    150,
    220,
    150,
    130,
    150,
    150,
    190,
    150,
    150,
)


def _percent(value: float) -> str:
    return f"{value:+.2f}%"


def _amount(value: float) -> str:
    return f"{value:+,.0f}"


def _property_value(value: float | None, *, percent: bool) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.2f}%" if percent else f"{value:,.2f}"


def _gain_value(
    status: QuantificationStatus,
    complete: float | None,
    partial: float | None,
    estimated: float | None = None,
) -> str:
    # 预估只决定显示数值；量化状态、分母与缺口仍保留在本行提示中。
    if estimated is not None:
        return _percent(estimated)
    if status == "complete":
        return "—" if complete is None else _percent(complete)
    if status == "partial":
        return "—" if partial is None else _percent(partial)
    if status == "not_applicable":
        return "+0.00%"
    return "—"


def _buff_value(
    *,
    status: QuantificationStatus,
    complete: float | None,
    partial: float | None,
    formatter,
) -> str:
    if status == "complete":
        return "—" if complete is None else formatter(complete)
    if status == "partial":
        return "—" if partial is None else formatter(partial)
    if status == "not_applicable":
        return "不适用"
    return "—"


def _buff_name(result: BattleBuffCounterfactualResult, *, text_catalog=None) -> str:
    if str(getattr(result, "method", "")).startswith("approximate_"):
        return localized_text(text_catalog, 'auto.features.battle_report.marginal.results.buff_name.f0a1f0065', '{buff_name}（估算）', buff_name=f'{result.buff_name}')
    return result.buff_name


def display_projection(
    *,
    candidate: float | None,
    heuristic: float | None,
    known: float | None,
) -> float | None:
    """优先候选，再用明确预估/已知投影；这是显示取值，不提升量化完整性。"""

    if candidate is not None:
        return candidate
    if heuristic is not None:
        return heuristic
    return known


def _quantification_tooltip(quantification, *, text_catalog=None) -> str:
    gaps = "\n".join(f"- {gap.explanation}" for gap in quantification.gaps)
    coverage = quantified_coverage_text(quantification)
    text = (
        localized_text(text_catalog, 'auto.features.battle_report.marginal.results.quantification_tooltip.fc9eb4bc9', '变化状态：{value}；量化率：{coverage}。', value=f'{quantification_status_text(quantification.status)}', coverage=f'{coverage}')
    )
    return text if not gaps else localized_text(text_catalog, 'auto.features.battle_report.marginal.results.quantification_tooltip.f715c3194', '{text}\n缺失依赖：\n{gaps}', text=f'{text}', gaps=f'{gaps}')


def render_attribute_results(
    table: QTableWidget,
    results: Sequence[BattleMarginalResult],
    *, text_catalog=None,
) -> None:
    table.setRowCount(len(results))
    for row_index, result in enumerate(results):
        role_status = _gain_status(
            result.quantification.status,
            result.role_denominator_status,
        )
        team_status = _gain_status(
            result.quantification.status,
            result.team_denominator_status,
        )
        unit = (
            f"+{result.unit * 100:.2f}%"
            if result.is_percent
            else f"+{result.unit:g}"
        )
        values = (
            f"{result.label} {unit}",
            _property_value(getattr(result, "panel_value", 0.0), percent=result.is_percent),
            _property_value(
                getattr(result, "weighted_effective_value", None),
                percent=result.is_percent,
            ),
            _gain_value(
                role_status,
                result.full_role_gain_percent,
                result.quantified_role_gain_percent,
                getattr(result, "estimated_role_gain_percent", None),
            ),
            _gain_value(
                team_status,
                result.full_team_gain_percent,
                result.quantified_team_gain_percent,
                getattr(result, "estimated_team_gain_percent", None),
            ),
            f"{getattr(result, 'related_role_share_percent', 0.0):.1f}%",
            f"{getattr(result, 'role_share_percent', result.damage_share_percent):.1f}%",
            f"{getattr(result, 'related_team_share_percent', 0.0):.1f}%",
        )
        tooltip = (
            resolve_text_catalog(text_catalog).render('frontend.battle_report.marginal.results.render_attribute_results.text', assumption=f'{result.assumption}', value=f'{quantification_status_text(result.role_denominator_status)}', value_2=f'{quantification_status_text(result.team_denominator_status)}', value_3=f'{_quantification_tooltip(result.quantification, text_catalog=text_catalog)}')
        )
        if result.property_id.startswith("DamagePenetrate"):
            tooltip += (
                "\n此处只显示角色侧面板穿透；精确调频等目标减抗已经进入"
                "目标抗性区基线，不计入角色穿透数值。"
            )
        if result.property_id in ("AtkAdd", "HPMaxAdd", "DefAdd"):
            tooltip += (
                "\n加权公式属性包含动态面板证据折算的等效固定增量，"
                "与逐击公式采用值一致；这不代表每个 Buff 的增量均已独立取证。"
            )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(tooltip)
            table.setItem(row_index, column, item)


def _gain_status(
    numerator_status: QuantificationStatus,
    denominator_status: QuantificationStatus,
) -> QuantificationStatus:
    if numerator_status == "not_applicable":
        return "not_applicable"
    if numerator_status == "unavailable" or denominator_status == "unavailable":
        return "unavailable"
    if numerator_status == "partial" or denominator_status == "partial":
        return "partial"
    return numerator_status


def _team_gain_text(result: BattleBuffCounterfactualResult) -> str:
    status = result.quantification.status
    if status == "complete":
        if result.damage_gain is None or result.gain_percent is None:
            return "—"
        return f"{result.damage_gain:+,.0f}（{result.gain_percent:+.2f}%）"
    if status == "partial":
        if (
            result.quantified_damage_gain is None
            or result.quantified_gain_percent is None
        ):
            return "—"
        return (
            f"{result.quantified_damage_gain:+,.0f}"
            f"（{result.quantified_gain_percent:+.2f}%）"
        )
    if status == "not_applicable":
        return "不适用"
    return "—"


def _unattributed_gain(result: BattleBuffCounterfactualResult) -> float | None:
    if result.quantification.status == "complete":
        return result.unattributed_damage_gain
    if result.quantification.status == "partial":
        return result.quantified_unattributed_damage_gain
    return None


def render_buff_benefit_results(
    table: QTableWidget,
    results: Sequence[BattleBuffCounterfactualResult],
    *,
    source_character_id: int | None,
    passive_results: Sequence[BattleBuffCounterfactualResult] = (),
    text_catalog=None,
) -> None:
    # 正式来源角色 ID 筛选 Buff/被动；逐受益者、未归因与未覆盖行分开，不把未知凑为零。
    filtered = tuple(
        result
        for result in (*results, *passive_results)
        if source_character_id is not None
        and result.source_character_id == source_character_id
    )
    rows = tuple(
        (result, beneficiary)
        for result in filtered
        for beneficiary in result.beneficiaries
    )
    unattributed = tuple(
        result
        for result in filtered
        if (
            (bool(result.beneficiaries)
             and _unattributed_gain(result) is not None
             and abs(_unattributed_gain(result) or 0.0) >= 0.5)
            or (not result.beneficiaries and result.affected_hits > 0)
        )
    )
    uncovered = tuple(
        result
        for result in filtered
        if not result.beneficiaries and result.affected_hits <= 0
    )
    table.setRowCount(len(rows) + len(unattributed) + len(uncovered))
    row_index = 0
    for result, beneficiary in rows:
        status = beneficiary.quantification.status
        team_contribution_status = (
            "partial"
            if result.quantification.status == "partial" and status == "complete"
            else status
        )
        values = (
            result.source_character_name,
            _buff_name(result, text_catalog=text_catalog),
            beneficiary.character_name,
            _buff_value(
                status=status,
                complete=beneficiary.damage_gain,
                partial=beneficiary.quantified_damage_gain,
                formatter=_amount,
            ),
            _buff_value(
                status=status,
                complete=beneficiary.recipient_gain_percent,
                partial=beneficiary.quantified_recipient_gain_percent,
                formatter=_percent,
            ),
            _buff_value(
                status=team_contribution_status,
                complete=beneficiary.team_contribution_percent,
                partial=beneficiary.quantified_team_contribution_percent,
                formatter=_percent,
            ),
            _team_gain_text(result),
            damage_coverage_text(
                getattr(beneficiary, "damage_coverage", None)
            , text_catalog=text_catalog),
            damage_coverage_text(getattr(result, "damage_coverage", None), text_catalog=text_catalog),
        )
        tooltip = (
            localized_text(text_catalog, 'auto.features.battle_report.marginal.results.render_buff_benefit_results.f9ac99035', '{explanation}\n作用范围：{target_scope}；置信度：{confidence}。\n{value}', explanation=f'{result.explanation}', target_scope=f'{result.target_scope}', confidence=f'{result.confidence}', value=f'{_quantification_tooltip(beneficiary.quantification, text_catalog=text_catalog)}')
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(plain_tooltip(resolve_text_catalog(text_catalog).render('ui.battle_report.render_buff_benefit_results.item', tooltip=f'{tooltip}', buff_asset_path=f'{result.buff_asset_path}')))
            table.setItem(row_index, column, item)
        row_index += 1
    for result in unattributed:
        gain = _unattributed_gain(result)
        denominator = (
            result.without_buff_damage
            if result.quantification.status == "complete"
            else result.without_quantified_effect_damage
        )
        team_gain = gain / denominator * 100.0 if gain is not None and denominator else None
        beneficiary_label = (
            "不适用"
            if result.quantification.status == "not_applicable"
            else "未量化"
            if not result.beneficiaries and gain is None
            else "无法归因"
        )
        values = (
            result.source_character_name,
            _buff_name(result, text_catalog=text_catalog),
            beneficiary_label,
            "—" if gain is None else f"{gain:+,.0f}",
            "—",
            "—" if team_gain is None else f"{team_gain:+.2f}%",
            _team_gain_text(result),
            "—",
            damage_coverage_text(getattr(result, "damage_coverage", None), text_catalog=text_catalog),
        )
        tooltip = f"{result.explanation}\n{_quantification_tooltip(result.quantification, text_catalog=text_catalog)}"
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(plain_tooltip(resolve_text_catalog(text_catalog).render('ui.battle_report.render_buff_benefit_results.item_2', tooltip=f'{tooltip}', buff_asset_path=f'{result.buff_asset_path}')))
            table.setItem(row_index, column, item)
        row_index += 1
    for result in uncovered:
        scope_label = (
            "当前范围未覆盖"
            if result.method == "not_covered"
            else "不适用"
            if result.quantification.status == "not_applicable"
            else "未量化"
        )
        values = (
            result.source_character_name,
            _buff_name(result, text_catalog=text_catalog),
            scope_label,
            "—",
            "—",
            "—",
            _team_gain_text(result),
            "—",
            damage_coverage_text(getattr(result, "damage_coverage", None), text_catalog=text_catalog),
        )
        tooltip = (
            f"{result.explanation}\n"
            f"{_quantification_tooltip(result.quantification, text_catalog=text_catalog)}"
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(plain_tooltip(resolve_text_catalog(text_catalog).render('ui.battle_report.render_buff_benefit_results.item_3', tooltip=f'{tooltip}', buff_asset_path=f'{result.buff_asset_path}')))
            table.setItem(row_index, column, item)
        row_index += 1


__all__ = [
    "BUFF_BENEFIT_HEADERS",
    "BUFF_BENEFIT_WIDTHS",
    "render_attribute_results",
    "render_buff_benefit_results",
]
