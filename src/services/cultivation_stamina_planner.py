# 统一养成计划的已有材料校验、来源过滤与确定副本体力计算。
"""Shared deterministic stamina projection for single and batch cultivation plans."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from src.domain.progression_material_conversion import (
    conversion_edges,
    lower_tier_ids,
    material_tier,
)
from src.domain.progression_stamina import (
    FarmingStage,
    MaterialRequirement,
    ProgressionStaminaRequest,
    ProgressionStaminaResult,
)
from src.services.cultivation_solver_process import calculate_isolated_progression_stamina


class CultivationMaterialLike(Protocol):
    item_id: str
    quantity: int


_CURRENCY_ITEM_IDS = frozenset({"Fons", "Gold"})


def stamina_material_ids(stages: tuple[FarmingStage, ...]) -> frozenset[str]:
    """Identify paid-stage materials, excluding currency from incidental drops."""

    ids = {
        item.item_id
        for stage in stages if stage.stamina_cost > 0
        for item in stage.yields
        if item.quantity > 0 and item.item_id not in _CURRENCY_ITEM_IDS
    }
    for item_id in tuple(ids):
        tier = material_tier(item_id)
        if tier is not None:
            family, level = tier
            ids.update(f"{family}_lv{higher}" for higher in range(level + 1, 4))
    return frozenset(ids)


def normalize_owned_quantities(values: Mapping[str, int]) -> dict[str, int]:
    return {
        str(item_id): nonnegative_owned(quantity)
        for item_id, quantity in values.items()
    }


def calculate_stamina_result(
    materials: Iterable[CultivationMaterialLike],
    owned_quantities: Mapping[str, int],
    stages: tuple[FarmingStage, ...],
    *,
    hunter_level: int,
    effective_identification_level: int | None,
) -> ProgressionStaminaResult:
    """Calculate deterministic material-stage stamina without passive drops."""

    stage_item_ids = {item.item_id for stage in stages for item in stage.yields}
    relevant = tuple(
        material for material in materials
        if material.item_id not in _CURRENCY_ITEM_IDS
        and (
            material.item_id in stage_item_ids
            or not is_non_stamina_source_material(material.item_id)
        )
    )
    item_ids = {material.item_id for material in relevant}
    for item_id in tuple(item_ids):
        item_ids.update(lower_tier_ids(item_id))
    requirements = tuple(
        MaterialRequirement(
            material.item_id,
            material.quantity,
            owned_quantities.get(material.item_id, 0),
        )
        for material in relevant
    ) + tuple(
        MaterialRequirement(item_id, 0, owned_quantities.get(item_id, 0))
        for item_id in sorted(item_ids - {item.item_id for item in relevant})
    )
    return calculate_isolated_progression_stamina(ProgressionStaminaRequest(
        hunter_level=int(hunter_level),
        effective_identification_level=effective_identification_level,
        requirements=requirements,
        stages=stages,
        conversions=conversion_edges(item_ids),
    ))


def nonnegative_owned(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("已有材料数量必须是非负整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("已有材料数量必须是非负整数") from exc
    if parsed < 0:
        raise ValueError("已有材料数量必须是非负整数")
    return parsed


def is_non_stamina_source_material(item_id: str) -> bool:
    """Return items obtained outside deterministic material-stage planning."""

    normalized = str(item_id)
    return normalized in {"Fons", "Gold"} or normalized.startswith((
        "OrdinaryMonMaterial_",
        "Worldboss_material_",
    ))


__all__ = [
    "calculate_stamina_result",
    "is_non_stamina_source_material",
    "normalize_owned_quantities",
    "stamina_material_ids",
]
