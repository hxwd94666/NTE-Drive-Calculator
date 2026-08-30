# 把反事实支持状态投影成只读矩阵，不生成任何执行开关。
"""Presentation-only counterfactual support matrix for 游戏资料库."""

from __future__ import annotations

from dataclasses import dataclass

from src.services.static_catalog_formula_service import (
    CounterfactualSupportEntry,
    StaticCatalogFormulaDomain,
    SupportStatus,
)

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


def _evidence_label(entry: CounterfactualSupportEntry) -> tuple[str, ...]:
    return tuple(
        f"{source.path}::{source.symbol} [{source.kind}] {source.note}"
        for source in entry.evidence
    )


def build_counterfactual_model_matrix(
    domain: StaticCatalogFormulaDomain,
) -> CounterfactualMatrixView:
    """Build a view-only matrix; its fields cannot select a production executor."""

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
