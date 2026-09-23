# 定义正式养成材料的三合一层级和已有库存抵扣规则。
"""Pure, explicitly allowlisted 3:1 progression-material conversions."""

from __future__ import annotations

import re
from collections.abc import Mapping


# 经验书与弧盘经验材料有独立经验值，绝不能按品质等级三合一。
_CONVERTIBLE_FAMILIES = frozenset({
    "EquipmentUpMaterial",
    *(f"OrdinaryMonMaterial_{index:02d}" for index in range(1, 5)),
    *(f"SkillUpMaterial_{index:02d}" for index in range(1, 6)),
    *(f"WeaponBreakMaterial_{index:02d}" for index in range(1, 6)),
})
_TIER = re.compile(r"^(.+)_lv([123])$")


def material_tier(item_id: str) -> tuple[str, int] | None:
    """Return a formal convertible family/tier, never a name-based guess."""

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
    """List only families used by this frozen calculation."""

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
    """Consume each owned unit once; fulfill lower-tier demands before crafting upward.

    ``available`` is deliberately mutable so an ordered batch can share one stock.
    The returned quantities are *effective* allocations at the requested tiers.
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


__all__ = ["allocate_owned", "conversion_edges", "lower_tier_ids", "material_tier"]
