# 定义固定轴反事实的量化状态、依赖缺口与伤害覆盖不变量。
"""Quantification contracts shared by fixed-axis counterfactual services."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose, isfinite
from typing import Literal


QuantificationStatus = Literal[
    "complete",
    "partial",
    "unavailable",
    "not_applicable",
]
DependencyScope = Literal[
    "character_only",
    "target_sensitive",
    "mechanic_specific",
]


@dataclass(frozen=True, slots=True)
class BattleQuantificationGap:
    """One changed formula dependency that lacks enough frozen evidence."""

    code: str
    dimension_id: str
    dependency_scope: DependencyScope
    property_ids: tuple[str, ...]
    explanation: str


@dataclass(frozen=True, slots=True)
class BattleCounterfactualRatio:
    """One safe formula ratio plus every cancelled or unresolved dimension."""

    status: QuantificationStatus
    quantified_ratio: float | None
    method: str
    confidence: str
    dependency_scope: DependencyScope
    included_dimension_ids: tuple[str, ...]
    cancelled_dimension_ids: tuple[str, ...]
    gaps: tuple[BattleQuantificationGap, ...]
    explanation: str

    def __post_init__(self) -> None:
        ratio = self.quantified_ratio
        if ratio is not None and (not isfinite(ratio) or ratio < 0.0):
            raise ValueError("quantified_ratio must be finite and non-negative")
        if self.status in {"complete", "partial", "not_applicable"} and ratio is None:
            raise ValueError(f"{self.status} requires a quantified ratio")
        if self.status == "unavailable" and ratio is not None:
            raise ValueError("unavailable must not expose a quantified ratio")
        if self.status == "complete" and self.gaps:
            raise ValueError("complete must not contain unresolved gaps")
        if self.status in {"partial", "unavailable"} and not self.gaps:
            raise ValueError(f"{self.status} requires at least one gap")
        if self.status == "partial" and not self.included_dimension_ids:
            raise ValueError("partial requires at least one quantified dimension")
        if self.status == "not_applicable":
            if ratio is None or not isclose(
                ratio,
                1.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError("not_applicable requires an exact ratio of 1")
            if self.gaps:
                raise ValueError("not_applicable must not contain gaps")
        overlap = set(self.included_dimension_ids) & set(
            self.cancelled_dimension_ids
        )
        if overlap:
            raise ValueError("included and cancelled dimensions must be disjoint")

    @classmethod
    def complete(
        cls,
        ratio: float,
        *,
        method: str,
        confidence: str,
        dependency_scope: DependencyScope,
        included_dimension_ids: tuple[str, ...] = (),
        cancelled_dimension_ids: tuple[str, ...] = (),
        explanation: str,
    ) -> BattleCounterfactualRatio:
        return cls(
            status="complete",
            quantified_ratio=ratio,
            method=method,
            confidence=confidence,
            dependency_scope=dependency_scope,
            included_dimension_ids=included_dimension_ids,
            cancelled_dimension_ids=cancelled_dimension_ids,
            gaps=(),
            explanation=explanation,
        )

    @classmethod
    def partial(
        cls,
        ratio: float,
        *,
        method: str,
        confidence: str,
        dependency_scope: DependencyScope,
        included_dimension_ids: tuple[str, ...],
        cancelled_dimension_ids: tuple[str, ...],
        gaps: tuple[BattleQuantificationGap, ...],
        explanation: str,
    ) -> BattleCounterfactualRatio:
        return cls(
            status="partial",
            quantified_ratio=ratio,
            method=method,
            confidence=confidence,
            dependency_scope=dependency_scope,
            included_dimension_ids=included_dimension_ids,
            cancelled_dimension_ids=cancelled_dimension_ids,
            gaps=gaps,
            explanation=explanation,
        )

    @classmethod
    def unavailable(
        cls,
        *,
        method: str,
        confidence: str,
        dependency_scope: DependencyScope,
        cancelled_dimension_ids: tuple[str, ...],
        gaps: tuple[BattleQuantificationGap, ...],
        explanation: str,
    ) -> BattleCounterfactualRatio:
        return cls(
            status="unavailable",
            quantified_ratio=None,
            method=method,
            confidence=confidence,
            dependency_scope=dependency_scope,
            included_dimension_ids=(),
            cancelled_dimension_ids=cancelled_dimension_ids,
            gaps=gaps,
            explanation=explanation,
        )

    @classmethod
    def not_applicable(
        cls,
        *,
        method: str,
        dependency_scope: DependencyScope = "character_only",
        cancelled_dimension_ids: tuple[str, ...] = (),
        explanation: str,
    ) -> BattleCounterfactualRatio:
        return cls(
            status="not_applicable",
            quantified_ratio=1.0,
            method=method,
            confidence="高",
            dependency_scope=dependency_scope,
            included_dimension_ids=(),
            cancelled_dimension_ids=cancelled_dimension_ids,
            gaps=(),
            explanation=explanation,
        )


@dataclass(frozen=True, slots=True)
class BattleDamageQuantification:
    """Aggregate damage buckets whose sum must equal ``basis_damage``."""

    status: QuantificationStatus
    basis_damage: float
    fully_quantified_damage: float
    partially_quantified_damage: float
    unavailable_damage: float
    proven_unchanged_damage: float
    quantified_increment: float | None
    gaps: tuple[BattleQuantificationGap, ...] = ()

    def __post_init__(self) -> None:
        buckets = (
            self.basis_damage,
            self.fully_quantified_damage,
            self.partially_quantified_damage,
            self.unavailable_damage,
            self.proven_unchanged_damage,
        )
        if any(not isfinite(value) or value < 0.0 for value in buckets):
            raise ValueError("damage quantification values must be finite and non-negative")
        bucket_total = sum(buckets[1:])
        tolerance = max(1e-9, self.basis_damage * 1e-9)
        if not isclose(
            bucket_total,
            self.basis_damage,
            rel_tol=0.0,
            abs_tol=tolerance,
        ):
            raise ValueError("damage quantification buckets must equal basis_damage")
        if self.quantified_increment is not None and not isfinite(
            self.quantified_increment
        ):
            raise ValueError("quantified_increment must be finite when present")
        if self.status in {"complete", "partial", "not_applicable"}:
            if self.quantified_increment is None:
                raise ValueError(f"{self.status} requires a quantified increment")
        elif self.quantified_increment is not None:
            raise ValueError("unavailable must not expose a quantified increment")
        if self.status in {"partial", "unavailable"} and not self.gaps:
            raise ValueError(f"{self.status} requires at least one gap")
        if self.status in {"complete", "not_applicable"} and self.gaps:
            raise ValueError(f"{self.status} must not contain gaps")
        if self.status == "complete" and (
            self.partially_quantified_damage > 0.0
            or self.unavailable_damage > 0.0
        ):
            raise ValueError("complete cannot contain partial or unavailable damage")
        if self.status == "partial" and (
            self.fully_quantified_damage <= 0.0
            and self.partially_quantified_damage <= 0.0
        ):
            raise ValueError("partial requires at least one quantified damage bucket")
        if self.status == "unavailable" and (
            self.fully_quantified_damage > 0.0
            or self.partially_quantified_damage > 0.0
        ):
            raise ValueError("unavailable cannot contain quantified damage")
        if self.status == "not_applicable" and not isclose(
            self.proven_unchanged_damage,
            self.basis_damage,
            rel_tol=0.0,
            abs_tol=tolerance,
        ):
            raise ValueError("not_applicable requires all basis damage unchanged")
        if self.status == "not_applicable":
            increment = self.quantified_increment
            if increment is None or not isclose(
                increment,
                0.0,
                rel_tol=0.0,
                abs_tol=tolerance,
            ):
                raise ValueError(
                    "not_applicable requires a zero quantified increment"
                )

    @classmethod
    def from_buckets(
        cls,
        *,
        status: QuantificationStatus,
        fully_quantified_damage: float = 0.0,
        partially_quantified_damage: float = 0.0,
        unavailable_damage: float = 0.0,
        proven_unchanged_damage: float = 0.0,
        quantified_increment: float | None = None,
        gaps: tuple[BattleQuantificationGap, ...] = (),
    ) -> BattleDamageQuantification:
        basis_damage = sum((
            fully_quantified_damage,
            partially_quantified_damage,
            unavailable_damage,
            proven_unchanged_damage,
        ))
        return cls(
            status=status,
            basis_damage=basis_damage,
            fully_quantified_damage=fully_quantified_damage,
            partially_quantified_damage=partially_quantified_damage,
            unavailable_damage=unavailable_damage,
            proven_unchanged_damage=proven_unchanged_damage,
            quantified_increment=quantified_increment,
            gaps=gaps,
        )


__all__ = [
    "BattleCounterfactualRatio",
    "BattleDamageQuantification",
    "BattleQuantificationGap",
    "DependencyScope",
    "QuantificationStatus",
]
