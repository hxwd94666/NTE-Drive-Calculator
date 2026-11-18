# 将固定轴量化状态投影为统一、不会把未知写成零的展示文本。
"""Presentation helpers for fixed-axis quantification states."""

from __future__ import annotations

from collections.abc import Callable

from src.domain.localization.catalog import localized_text
from src.domain.battle_report.counterfactual.quantification import BattleDamageQuantification, QuantificationStatus
from src.domain.battle_report.counterfactual.results import BattleDamageCoverage


_STATUS_TEXT: dict[QuantificationStatus, str] = {
    "complete": "完整",
    "partial": "部分",
    "unavailable": "未量化",
    "not_applicable": "不适用",
}


def quantification_status_text(status: QuantificationStatus) -> str:
    return _STATUS_TEXT[status]


def quantified_coverage_percent(
    quantification: BattleDamageQuantification,
) -> float:
    if quantification.basis_damage <= 0.0:
        return 0.0
    return (
        quantification.fully_quantified_damage
        + quantification.partially_quantified_damage
    ) / quantification.basis_damage * 100.0


def quantified_coverage_text(
    quantification: BattleDamageQuantification,
) -> str:
    if quantification.status == "not_applicable":
        return "不适用"
    return f"{quantified_coverage_percent(quantification):.1f}%"


def damage_coverage_text(
    coverage: BattleDamageCoverage | None, *, text_catalog=None,
) -> str:
    if coverage is None or coverage.basis_damage <= 0.0:
        return "—"
    covered = coverage.covered_percent or 0.0
    unresolved = coverage.unresolved_percent or 0.0
    if unresolved > 0.0:
        if covered > 0.0:
            return localized_text(text_catalog, 'auto.features.battle_report.analysis.quantification.damage_coverage_text.f7f8a48ff', '至少 {covered}%（另 {unresolved}% 未判定）', covered=f'{covered:.1f}', unresolved=f'{unresolved:.1f}')
        return localized_text(text_catalog, 'auto.features.battle_report.analysis.quantification.damage_coverage_text.f84dfdb42', '未判定 {unresolved}%', unresolved=f'{unresolved:.1f}')
    return f"{covered:.1f}%"


def format_quantified_value(
    *,
    status: QuantificationStatus,
    complete_value: float | None,
    quantified_value: float | None,
    formatter: Callable[[float], str], text_catalog=None,
) -> str:
    if status == "complete":
        return "—" if complete_value is None else formatter(complete_value)
    if status == "partial":
        return (
            "—"
            if quantified_value is None
            else localized_text(text_catalog, 'auto.features.battle_report.analysis.quantification.format_quantified_value.f33760be3', '已量化 {value}', value=f'{formatter(quantified_value)}')
        )
    if status == "not_applicable":
        return "不适用"
    return "—"


__all__ = [
    "damage_coverage_text",
    "format_quantified_value",
    "quantification_status_text",
    "quantified_coverage_percent",
    "quantified_coverage_text",
]
