# 将弧盘等级、突破和经验材料投影为精确的养成需求。
"""Qt-free fork progression requirement projection."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from src.domain.progression_materials import (
    ExperienceMaterial,
    least_waste_experience_materials,
)
from src.services.character_progression_requirements import (
    CharacterMaterialRequirement,
    MaterialSummaryStatus,
    ProgressionRequirementGap,
)
from src.services.static_catalog_fork_service import ForkCatalogDetail


@dataclass(frozen=True, slots=True)
class ForkLevelMaterialProjection:
    status: MaterialSummaryStatus
    required_experience: int
    experience_overflow: int
    experience_materials: tuple[CharacterMaterialRequirement, ...]
    breakthrough_materials: tuple[CharacterMaterialRequirement, ...]
    experience_costs: tuple[CharacterMaterialRequirement, ...]
    breakthrough_costs: tuple[CharacterMaterialRequirement, ...]
    included_breakthrough_stages: tuple[int, ...]
    gaps: tuple[ProgressionRequirementGap, ...]


def project_fork_level_requirements(
    detail: ForkCatalogDetail,
    *,
    current_level: int,
    current_stage: int,
    target_level: int,
    target_stage: int,
) -> ForkLevelMaterialProjection:
    """Aggregate official fork EXP, material-use costs and crossed gates."""

    gaps: list[ProgressionRequirementGap] = []
    growth = {row.level: row for row in detail.growth_levels}
    required_experience = 0
    for level in range(int(current_level) + 1, int(target_level) + 1):
        row = growth.get(level)
        if row is None:
            gaps.append(ProgressionRequirementGap(
                reason_code="fork_level_exp_row_unavailable",
                level=level,
            ))
            continue
        required_experience += row.need_exp

    quantities, overflow = least_waste_experience_materials(
        tuple(
            ExperienceMaterial(item.item_id, item.experience_value)
            for item in detail.experience_materials
        ),
        required_experience,
    )
    experience_materials = tuple(
        CharacterMaterialRequirement(item.item_id, item.quantity)
        for item in quantities
    )
    if required_experience > 0 and not experience_materials:
        gaps.append(ProgressionRequirementGap(
            reason_code="fork_exp_material_unavailable",
        ))

    experience_costs: dict[str, int] = defaultdict(int)
    specs = {item.item_id: item for item in detail.experience_materials}
    for requirement in experience_materials:
        for cost in specs[requirement.item_id].costs:
            if cost.amount is None or cost.amount <= 0:
                gaps.append(ProgressionRequirementGap(
                    reason_code="fork_exp_material_cost_invalid",
                    item_id=requirement.item_id,
                ))
                continue
            experience_costs[_canonical_item_id(cost.item_id)] += (
                cost.amount * requirement.required_quantity
            )

    breakthrough: dict[str, int] = defaultdict(int)
    breakthrough_costs: dict[str, int] = defaultdict(int)
    included: list[int] = []
    rows = {row.stage: row for row in detail.breakthroughs}
    for stage in range(int(current_stage) + 1, int(target_stage) + 1):
        row = rows.get(stage)
        if row is None:
            gaps.append(ProgressionRequirementGap(
                reason_code="fork_breakthrough_cost_row_unavailable",
                level=stage,
            ))
            continue
        included.append(stage)
        for cost in (*row.item_costs, *row.gold_costs):
            item_id = _canonical_item_id(cost.item_id)
            if cost.amount is None or cost.amount <= 0:
                gaps.append(ProgressionRequirementGap(
                    reason_code="fork_breakthrough_cost_invalid",
                    level=stage,
                    item_id=item_id,
                ))
                continue
            target = breakthrough_costs if item_id in {"Fons", "Gold"} else breakthrough
            target[item_id] += cost.amount

    has_results = bool(
        experience_materials
        or breakthrough
        or experience_costs
        or breakthrough_costs
    )
    status = (
        MaterialSummaryStatus.PARTIAL
        if gaps and has_results
        else MaterialSummaryStatus.UNAVAILABLE
        if gaps
        else MaterialSummaryStatus.COMPLETE
    )
    return ForkLevelMaterialProjection(
        status=status,
        required_experience=required_experience,
        experience_overflow=overflow,
        experience_materials=experience_materials,
        breakthrough_materials=_requirements(breakthrough),
        experience_costs=_requirements(experience_costs),
        breakthrough_costs=_requirements(breakthrough_costs),
        included_breakthrough_stages=tuple(included),
        gaps=tuple(gaps),
    )


def _canonical_item_id(item_id: str) -> str:
    return "Gold" if str(item_id) == "gold" else str(item_id)


def _requirements(
    totals: dict[str, int],
) -> tuple[CharacterMaterialRequirement, ...]:
    return tuple(
        CharacterMaterialRequirement(item_id, quantity)
        for item_id, quantity in sorted(totals.items())
        if quantity > 0
    )


__all__ = ["ForkLevelMaterialProjection", "project_fork_level_requirements"]
