# 提供角色与弧盘共用的经验材料最小浪费换算。
"""Pure deterministic experience-material optimization."""

from __future__ import annotations

from dataclasses import dataclass
from functools import reduce
from math import gcd


@dataclass(frozen=True, slots=True)
class ExperienceMaterial:
    item_id: str
    experience_value: int


@dataclass(frozen=True, slots=True)
class MaterialQuantity:
    item_id: str
    quantity: int


def least_waste_experience_materials(
    materials: tuple[ExperienceMaterial, ...],
    required_experience: int,
) -> tuple[tuple[MaterialQuantity, ...], int]:
    """Minimize overflow first, item count second, with stable tie breaking."""

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
        MaterialQuantity(item_id=material.item_id, quantity=quantity)
        for material, quantity in zip(usable, material_counts)
        if quantity > 0
    )
    return requirements, target * divisor - required_experience


__all__ = [
    "ExperienceMaterial",
    "MaterialQuantity",
    "least_waste_experience_materials",
]
