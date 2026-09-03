# 提供角色养成材料聚合的纯领域契约与计算。
"""Qt-free material aggregation contracts for character cultivation pages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from functools import reduce
from math import gcd

from src.services.static_catalog_character_models import (
    CharacterExperienceMaterial,
    CharacterProgressionProfile,
    CharacterSkill,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


class MaterialSummaryStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CharacterMaterialRequirement:
    item_id: str
    required_quantity: int


@dataclass(frozen=True, slots=True)
class ProgressionRequirementGap:
    reason_code: str
    level: int | None = None
    item_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProgressionRequirementProjection:
    status: MaterialSummaryStatus
    requirements: tuple[CharacterMaterialRequirement, ...]
    gaps: tuple[ProgressionRequirementGap, ...]


@dataclass(frozen=True, slots=True)
class CharacterLevelMaterialProjection:
    status: MaterialSummaryStatus
    required_experience: int
    experience_overflow: int
    experience_books: tuple[CharacterMaterialRequirement, ...]
    breakthrough_materials: tuple[CharacterMaterialRequirement, ...]
    additional_costs: tuple[CharacterMaterialRequirement, ...]
    included_breakthrough_stages: tuple[int, ...]
    gaps: tuple[ProgressionRequirementGap, ...]


def project_character_level_requirements(
    profile: CharacterProgressionProfile,
    *,
    from_level: int,
    to_level: int,
    include_breakthroughs: bool,
) -> CharacterLevelMaterialProjection:
    """Summarize formal costs; never convert requirements into stamina."""

    start = int(from_level)
    end = int(to_level)
    if start < 1 or end > 80 or start >= end:
        return CharacterLevelMaterialProjection(
            status=MaterialSummaryStatus.UNAVAILABLE,
            required_experience=0,
            experience_overflow=0,
            experience_books=(),
            breakthrough_materials=(),
            additional_costs=(),
            included_breakthrough_stages=(),
            gaps=(ProgressionRequirementGap(
                reason_code="character_level_interval_invalid",
            ),),
        )

    level_rows = {row.level: row for row in profile.upgrade_levels}
    gaps: list[ProgressionRequirementGap] = []
    required_experience = 0
    for level in range(start, end):
        row = level_rows.get(level)
        if row is None:
            gaps.append(ProgressionRequirementGap(
                reason_code="character_level_exp_row_unavailable",
                level=level,
            ))
            continue
        required_experience += row.need_exp

    books, overflow = _least_waste_experience_books(
        profile.experience_materials,
        required_experience,
    )
    if required_experience > 0 and not books:
        gaps.append(ProgressionRequirementGap(
            reason_code="character_exp_material_unavailable",
        ))

    additional: dict[str, int] = {}
    materials_by_id = {
        material.item_id: material for material in profile.experience_materials
    }
    for book in books:
        material = materials_by_id[book.item_id]
        for cost in material.costs:
            additional[cost.item_id] = (
                additional.get(cost.item_id, 0)
                + cost.quantity * book.required_quantity
            )

    breakthrough_totals: dict[str, int] = {}
    included_stages: list[int] = []
    stages = sorted(profile.breakthrough_stages, key=lambda item: item.stage)
    if include_breakthroughs:
        previous_cap: int | None = None
        for stage in stages:
            if stage.stage == 0:
                previous_cap = stage.max_character_level
                continue
            if previous_cap is None:
                gaps.append(ProgressionRequirementGap(
                    reason_code="character_breakthrough_stage_zero_unavailable",
                ))
                break
            if start <= previous_cap < end:
                included_stages.append(stage.stage)
                for cost in stage.costs:
                    target = (
                        additional if cost.item_id == "Fons"
                        else breakthrough_totals
                    )
                    target[cost.item_id] = target.get(cost.item_id, 0) + cost.quantity
            previous_cap = stage.max_character_level

    status = (
        MaterialSummaryStatus.PARTIAL
        if gaps and (books or breakthrough_totals or additional)
        else MaterialSummaryStatus.UNAVAILABLE
        if gaps
        else MaterialSummaryStatus.COMPLETE
    )
    return CharacterLevelMaterialProjection(
        status=status,
        required_experience=required_experience,
        experience_overflow=overflow,
        experience_books=books,
        breakthrough_materials=_requirements(breakthrough_totals),
        additional_costs=_requirements(additional),
        included_breakthrough_stages=tuple(included_stages),
        gaps=tuple(gaps),
    )


def _least_waste_experience_books(
    materials: tuple[CharacterExperienceMaterial, ...],
    required_experience: int,
) -> tuple[tuple[CharacterMaterialRequirement, ...], int]:
    if required_experience <= 0:
        return (), 0
    usable = tuple(sorted(
        (item for item in materials if item.experience_value > 0),
        key=lambda item: (-item.experience_value, item.item_id),
    ))
    if not usable:
        return (), 0
    divisor = reduce(gcd, (item.experience_value for item in usable))
    values = tuple(item.experience_value // divisor for item in usable)
    minimum = (required_experience + divisor - 1) // divisor
    limit = minimum + max(values) ** 2
    unreachable = limit + 1
    counts = [unreachable] * (limit + 1)
    choices = [-1] * (limit + 1)
    counts[0] = 0
    for amount in range(1, limit + 1):
        for index, value in enumerate(values):
            if amount < value or counts[amount - value] == unreachable:
                continue
            candidate = counts[amount - value] + 1
            if candidate < counts[amount]:
                counts[amount] = candidate
                choices[amount] = index
    target = next(
        (amount for amount in range(minimum, limit + 1) if choices[amount] >= 0),
        None,
    )
    if target is None:
        return (), 0
    material_counts = [0] * len(usable)
    cursor = target
    while cursor > 0:
        index = choices[cursor]
        if index < 0:
            return (), 0
        material_counts[index] += 1
        cursor -= values[index]
    requirements = tuple(
        CharacterMaterialRequirement(
            item_id=material.item_id,
            required_quantity=quantity,
        )
        for material, quantity in zip(usable, material_counts)
        if quantity > 0
    )
    return requirements, target * divisor - required_experience


def _requirements(
    totals: dict[str, int],
) -> tuple[CharacterMaterialRequirement, ...]:
    return tuple(
        CharacterMaterialRequirement(item_id=item_id, required_quantity=quantity)
        for item_id, quantity in sorted(totals.items())
        if quantity > 0
    )


def project_skill_level_requirements(
    skill: CharacterSkill,
    *,
    from_level: int,
    to_level: int,
    terminology: StaticCatalogTerminologyService | None,
) -> ProgressionRequirementProjection:
    """Aggregate formal per-level item costs without any stamina conversion."""

    start = int(from_level)
    end = int(to_level)
    if start >= end:
        return ProgressionRequirementProjection(
            status=MaterialSummaryStatus.UNAVAILABLE,
            requirements=(),
            gaps=(ProgressionRequirementGap(
                reason_code="skill_level_interval_invalid",
            ),),
        )
    levels = {level.level: level for level in skill.levels}
    totals: dict[str, int] = {}
    gaps: list[ProgressionRequirementGap] = []
    for level_value in range(start, end):
        target_level = level_value + 1
        level = levels.get(level_value)
        if level is None:
            gaps.append(ProgressionRequirementGap(
                reason_code="skill_level_cost_row_unavailable",
                level=target_level,
            ))
            continue
        for item in level.costs:
            canonical_id = _canonical_item_id(
                item.item_id,
                terminology=terminology,
            )
            if item.hidden_amount:
                gaps.append(ProgressionRequirementGap(
                    reason_code="skill_cost_quantity_hidden",
                    level=target_level,
                    item_id=canonical_id,
                ))
                continue
            quantity = float(item.quantity)
            if quantity <= 0 or not quantity.is_integer():
                gaps.append(ProgressionRequirementGap(
                    reason_code="skill_cost_quantity_invalid",
                    level=target_level,
                    item_id=canonical_id,
                ))
                continue
            totals[canonical_id] = totals.get(canonical_id, 0) + int(quantity)
    requirements = tuple(
        CharacterMaterialRequirement(
            item_id=item_id,
            required_quantity=quantity,
        )
        for item_id, quantity in sorted(totals.items())
    )
    status = (
        MaterialSummaryStatus.PARTIAL
        if gaps and requirements
        else MaterialSummaryStatus.UNAVAILABLE
        if gaps
        else MaterialSummaryStatus.COMPLETE
    )
    return ProgressionRequirementProjection(
        status=status,
        requirements=requirements,
        gaps=tuple(gaps),
    )


def _canonical_item_id(
    item_id: str,
    *,
    terminology: StaticCatalogTerminologyService | None,
) -> str:
    stable_id = str(item_id or "").strip()
    if terminology is None or not stable_id:
        return stable_id
    term = terminology.resolve(
        "item",
        stable_id,
        context="progression_cost",
    )
    return term.canonical_id or stable_id


__all__ = [
    "CharacterLevelMaterialProjection",
    "CharacterMaterialRequirement",
    "MaterialSummaryStatus",
    "ProgressionRequirementGap",
    "ProgressionRequirementProjection",
    "project_character_level_requirements",
    "project_skill_level_requirements",
]
