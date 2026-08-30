# 把公式与反事实领域记录投影成不依赖 Qt 的只读展示数据。
"""Qt-free formula details and counterfactual support projections."""

from __future__ import annotations

from dataclasses import dataclass

from src.services.static_catalog_formula_service import (
    CatalogEvidenceReference,
    CounterfactualSupportEntry,
    FormulaEntry,
    StaticCatalogFormulaDomain,
    SupportStatus,
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
_STATUS_LABELS: dict[SupportStatus, str] = {
    "complete": "完整",
    "partial": "部分",
    "unavailable": "不可用",
    "not_applicable": "不适用",
}
_STATUS_ORDER: tuple[SupportStatus, ...] = (
    "complete",
    "partial",
    "unavailable",
    "not_applicable",
)


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


@dataclass(frozen=True, slots=True)
class CounterfactualMatrixRow:
    key: str
    category: str
    mechanism: str
    scope: str
    status: SupportStatus
    status_label: str
    modeling_scheme: str
    evidence: tuple[str, ...]
    consumer_entries: tuple[str, ...]
    gap_codes: tuple[str, ...]
    covered_dataset: str
    covered_entities: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CounterfactualMatrixView:
    projection_version: str
    readonly_notice: str
    status_counts: tuple[tuple[SupportStatus, int], ...]
    rows: tuple[CounterfactualMatrixRow, ...]


def _source_view(source: CatalogEvidenceReference) -> FormulaSourceView:
    return FormulaSourceView(
        source_type=_EVIDENCE_LABELS[source.kind],
        location=source.path,
        symbol=source.symbol,
        note=source.note,
    )


def _formula_detail(entry: FormulaEntry) -> FormulaDetailView:
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
        grouped[entry.section].append(_formula_detail(entry))
    return tuple(
        FormulaDetailSectionView(title=section, formulas=tuple(grouped[section]))
        for section in order
    )


def _evidence_label(entry: CounterfactualSupportEntry) -> tuple[str, ...]:
    return tuple(
        f"{source.path}::{source.symbol} [{source.kind}] {source.note}"
        for source in entry.evidence
    )


def build_counterfactual_model_matrix(
    domain: StaticCatalogFormulaDomain,
) -> CounterfactualMatrixView:
    """Build a read-only matrix that cannot select a production executor."""

    rows = tuple(
        CounterfactualMatrixRow(
            key=entry.key,
            category=entry.category,
            mechanism=entry.mechanism,
            scope=entry.scope,
            status=entry.status,
            status_label=_STATUS_LABELS[entry.status],
            modeling_scheme=entry.modeling_scheme,
            evidence=_evidence_label(entry),
            consumer_entries=entry.consumer_entries,
            gap_codes=entry.gap_codes,
            covered_dataset=entry.covered_dataset,
            covered_entities=entry.covered_entities,
            limitations=entry.limitations,
        )
        for entry in domain.counterfactual_support
    )
    return CounterfactualMatrixView(
        projection_version=domain.projection_version,
        readonly_notice=(
            "状态仅描述当前证据和消费者覆盖，不控制 C++、Python 或生产反事实计算。"
        ),
        status_counts=tuple(
            (status, sum(row.status == status for row in rows))
            for status in _STATUS_ORDER
        ),
        rows=rows,
    )


__all__ = [
    "CounterfactualMatrixRow",
    "CounterfactualMatrixView",
    "FormulaDetailSectionView",
    "FormulaDetailView",
    "FormulaSourceView",
    "build_counterfactual_model_matrix",
    "build_formula_detail_sections",
]
