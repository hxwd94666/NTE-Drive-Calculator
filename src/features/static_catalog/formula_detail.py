# 把公式领域记录投影成不依赖 Qt 的详情页数据。
"""Presentation-only rows for the 游戏资料库 formula detail pane."""

from __future__ import annotations

from dataclasses import dataclass

from src.services.static_catalog_formula_service import (
    CatalogEvidenceReference,
    FormulaEntry,
    StaticCatalogFormulaDomain,
)

_EVIDENCE_LABELS = {
    "project_contract": "项目规则",
    "implementation": "当前实现",
    "public_behavior_test": "公共行为测试",
    "official_static": "官方静态输入",
    "repository_audit": "仓库审计",
}
_BOUNDARY_LABELS = {
    "project_rule": "项目规则",
    "official_static_input": "官方静态输入",
    "runtime_derived": "运行时派生",
    "observed_runtime": "运行时观测",
}


@dataclass(frozen=True, slots=True)
class FormulaSourceView:
    source_type: str
    location: str
    symbol: str
    note: str


@dataclass(frozen=True, slots=True)
class FormulaDetailView:
    key: str
    section: str
    title: str
    expression: str
    boundary_label: str
    variables: tuple[tuple[str, str], ...]
    applicable_when: tuple[str, ...]
    limitations: tuple[str, ...]
    sources: tuple[FormulaSourceView, ...]


@dataclass(frozen=True, slots=True)
class FormulaDetailSectionView:
    title: str
    formulas: tuple[FormulaDetailView, ...]


def _source_view(source: CatalogEvidenceReference) -> FormulaSourceView:
    return FormulaSourceView(
        source_type=_EVIDENCE_LABELS[source.kind],
        location=source.path,
        symbol=source.symbol,
        note=source.note,
    )


def _detail(entry: FormulaEntry) -> FormulaDetailView:
    return FormulaDetailView(
        key=entry.key,
        section=entry.section,
        title=entry.title,
        expression=entry.expression,
        boundary_label=_BOUNDARY_LABELS[entry.boundary],
        variables=tuple((row.symbol, row.meaning) for row in entry.variables),
        applicable_when=entry.applicable_when,
        limitations=entry.limitations,
        sources=tuple(_source_view(source) for source in entry.evidence),
    )


def build_formula_detail_sections(
    domain: StaticCatalogFormulaDomain,
) -> tuple[FormulaDetailSectionView, ...]:
    """Group formulas without creating widgets or reinterpreting evidence."""

    order: list[str] = []
    grouped: dict[str, list[FormulaDetailView]] = {}
    for entry in domain.formulas:
        if entry.section not in grouped:
            grouped[entry.section] = []
            order.append(entry.section)
        grouped[entry.section].append(_detail(entry))
    return tuple(
        FormulaDetailSectionView(title=section, formulas=tuple(grouped[section]))
        for section in order
    )
