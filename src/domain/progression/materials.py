# 统一养成材料的经验换算、正式三合一关系与共享库存抵扣。
"""经验材料按经验值换算；只有白名单突破/技能材料允许三合一，二者不混用。"""

from __future__ import annotations

import re
from collections.abc import Mapping
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
    """先最小化经验溢出，再最小化材料件数，同分按稳定材料顺序选择。"""

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


# 经验书与弧盘经验材料有独立经验值，绝不能按品质等级三合一。
_CONVERTIBLE_FAMILIES = frozenset({
    "EquipmentUpMaterial",
    *(f"OrdinaryMonMaterial_{index:02d}" for index in range(1, 5)),
    *(f"SkillUpMaterial_{index:02d}" for index in range(1, 6)),
    *(f"WeaponBreakMaterial_{index:02d}" for index in range(1, 6)),
})
_TIER = re.compile(r"^(.+)_lv([123])$")


def material_tier(item_id: str) -> tuple[str, int] | None:
    """仅接受正式白名单材料族与相邻等级，不根据名称或品质推测转换。"""

    match = _TIER.fullmatch(str(item_id))
    if match is None or match.group(1) not in _CONVERTIBLE_FAMILIES:
        return None
    return match.group(1), int(match.group(2))


def lower_tier_ids(item_id: str) -> tuple[str, ...]:
    tier = material_tier(item_id)
    if tier is None:
        return ()
    family, level = tier
    return tuple(f"{family}_lv{index}" for index in range(1, level))


def conversion_edges(item_ids: Mapping[str, int] | set[str] | tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """只生成本次冻结输入实际使用材料族的相邻转换关系。"""

    families = {tier[0] for item_id in item_ids if (tier := material_tier(item_id))}
    return tuple(
        (f"{family}_lv{level}", f"{family}_lv{level + 1}")
        for family in sorted(families)
        for level in (1, 2)
    )


def allocate_owned(
    requirements: Mapping[str, int],
    available: dict[str, int],
) -> dict[str, int]:
    """低阶需求优先抵扣，再按三合一向上合成，每份已有材料只消费一次。

    available 是本次有序批量计算共享的可变草稿库存，不回写背包。
    返回需求等级的有效抵扣量。
    """

    allocated: dict[str, int] = {}
    ordered = sorted(
        requirements,
        key=lambda item_id: (
            material_tier(item_id)[0] if material_tier(item_id) else item_id,
            material_tier(item_id)[1] if material_tier(item_id) else 0,
            item_id,
        ),
    )
    for item_id in ordered:
        required = max(0, int(requirements[item_id]))
        tier = material_tier(item_id)
        if tier is not None and required > available.get(item_id, 0):
            _craft_up(tier[0], tier[1], required - available.get(item_id, 0), available)
        quantity = min(required, available.get(item_id, 0))
        available[item_id] = available.get(item_id, 0) - quantity
        allocated[item_id] = quantity
    return allocated


def _craft_up(family: str, level: int, needed: int, available: dict[str, int]) -> None:
    if level <= 1 or needed <= 0:
        return
    lower_id = f"{family}_lv{level - 1}"
    higher_id = f"{family}_lv{level}"
    short = max(0, needed * 3 - available.get(lower_id, 0))
    if short and level > 2:
        _craft_up(family, level - 1, short, available)
    crafted = min(needed, available.get(lower_id, 0) // 3)
    available[lower_id] = available.get(lower_id, 0) - 3 * crafted
    available[higher_id] = available.get(higher_id, 0) + crafted
