# 渲染固定轴属性与 Buff 的分层量化结果，未知值统一显示为破折号。
"""Table renderers for fixed-axis marginal results."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from src.domain.battle_buff_counterfactual import BattleBuffCounterfactualResult
from src.domain.battle_counterfactual import BattleMarginalResult
from src.features.battle_report.marginal_quantification_view import (
    format_quantified_value,
    quantification_status_text,
    quantified_coverage_text,
)


def _percent(value: float) -> str:
    return f"{value:+.2f}%"


def _amount(value: float) -> str:
    return f"{value:+,.0f}"


def display_projection(
    *,
    candidate: float | None,
    heuristic: float | None,
    known: float | None,
) -> float | None:
    """Prefer the full candidate while retaining conservative fallbacks."""

    if candidate is not None:
        return candidate
    if heuristic is not None:
        return heuristic
    return known


def _quantification_tooltip(quantification) -> str:
    gaps = "\n".join(f"- {gap.explanation}" for gap in quantification.gaps)
    coverage = quantified_coverage_text(quantification)
    text = (
        f"变化状态：{quantification_status_text(quantification.status)}；"
        f"量化率：{coverage}。"
    )
    return text if not gaps else f"{text}\n缺失依赖：\n{gaps}"


def render_attribute_results(
    table: QTableWidget,
    results: Sequence[BattleMarginalResult],
) -> None:
    table.setRowCount(len(results))
    for row_index, result in enumerate(results):
        status = result.quantification.status
        unit = (
            f"+{result.unit * 100:.2f}%"
            if result.is_percent
            else f"+{result.unit:g}"
        )
        values = (
            f"{result.label} {unit}",
            quantification_status_text(status),
            format_quantified_value(
                status=status,
                complete_value=result.full_role_gain_percent,
                quantified_value=result.quantified_role_gain_percent,
                formatter=_percent,
            ),
            format_quantified_value(
                status=status,
                complete_value=result.full_team_gain_percent,
                quantified_value=result.quantified_team_gain_percent,
                formatter=_percent,
            ),
            quantified_coverage_text(result.quantification),
            f"{result.damage_share_percent:.1f}%",
            format_quantified_value(
                status=status,
                complete_value=result.quantification.quantified_increment,
                quantified_value=result.quantification.quantified_increment,
                formatter=_amount,
            ),
            result.assumption,
        )
        tooltip = (
            f"{result.assumption}\n"
            f"{_quantification_tooltip(result.quantification)}"
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(tooltip)
            table.setItem(row_index, column, item)


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
            f"已量化 {result.quantified_damage_gain:+,.0f}"
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
) -> None:
    filtered = tuple(
        result
        for result in results
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
            (_unattributed_gain(result) is not None
             and abs(_unattributed_gain(result) or 0.0) >= 0.5)
            or (not result.beneficiaries and result.affected_hits > 0)
        )
    )
    table.setRowCount(len(rows) + len(unattributed))
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
            result.buff_name,
            beneficiary.character_name,
            format_quantified_value(
                status=status,
                complete_value=beneficiary.damage_gain,
                quantified_value=beneficiary.quantified_damage_gain,
                formatter=_amount,
            ),
            format_quantified_value(
                status=status,
                complete_value=beneficiary.recipient_gain_percent,
                quantified_value=beneficiary.quantified_recipient_gain_percent,
                formatter=_percent,
            ),
            format_quantified_value(
                status=team_contribution_status,
                complete_value=beneficiary.team_contribution_percent,
                quantified_value=beneficiary.quantified_team_contribution_percent,
                formatter=_percent,
            ),
            _team_gain_text(result),
            quantified_coverage_text(beneficiary.quantification),
        )
        tooltip = (
            f"{result.explanation}\n作用范围：{result.target_scope}；"
            f"置信度：{result.confidence}。\n"
            f"{_quantification_tooltip(beneficiary.quantification)}"
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(tooltip)
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
        prefix = "已量化 " if result.quantification.status == "partial" else ""
        values = (
            result.source_character_name,
            result.buff_name,
            "无法归因",
            "—" if gain is None else f"{prefix}{gain:+,.0f}",
            "—",
            "—" if team_gain is None else f"{prefix}{team_gain:+.2f}%",
            _team_gain_text(result),
            quantified_coverage_text(result.quantification),
        )
        tooltip = f"{result.explanation}\n{_quantification_tooltip(result.quantification)}"
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(tooltip)
            table.setItem(row_index, column, item)
        row_index += 1


__all__ = ["render_attribute_results", "render_buff_benefit_results"]
