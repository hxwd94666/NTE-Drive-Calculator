# 统一养成计划的已有材料校验、来源过滤与平均产出体力估算。
"""Shared source-aware stamina estimates for single and batch cultivation plans."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from src.domain.progression.materials import (
    conversion_edges,
    lower_tier_ids,
    material_tier,
)
from src.domain.progression.stamina import (
    FarmingStage,
    MaterialRequirement,
    ProgressionStaminaRequest,
    ProgressionStaminaResult,
)
from src.integrations.progression.solver_process import calculate_isolated_progression_stamina
from src.services.progression.estimates import add_boss_estimates, with_material_gaps
from src.domain.progression.stamina import project_identification_level


class CultivationMaterialLike(Protocol):
    item_id: str
    quantity: int


_NON_STAMINA_CURRENCY_ITEM_IDS = frozenset({"Fons"})

# 已逐项核对 Monster/DT_MonsterDropBox → ClientDropGroupDataTable →
# DropSequenceDataTable 的正式 ID：七种突破材料均来自每次 60 体力的领奖箱。
# 这里只声明付费来源，不声明世界等级、数量或掉落效率；不按名称前缀扩展身份。
_WORLD_BOSS_STAMINA_ITEM_IDS = frozenset({
    "Worldboss_material_01", "Worldboss_material_02", "Worldboss_material_03",
    "Worldboss_material_04", "Worldboss_material_05", "Worldboss_material_06",
    "Worldboss_material_07",
})


def is_world_boss_stamina_material(item_id: str) -> bool:
    return item_id in _WORLD_BOSS_STAMINA_ITEM_IDS


def stamina_material_ids(
    stages: tuple[FarmingStage, ...],
    *,
    materials: Iterable[CultivationMaterialLike] = (),
) -> frozenset[str]:
    """付费来源与确定产出分开：已核实来源的材料不因掉落效率未知而隐藏。"""

    ids = {"Gold"} | {
        item.item_id
        for stage in stages if stage.stamina_cost > 0
        for item in stage.yields
        if item.quantity > 0 and item.item_id not in _NON_STAMINA_CURRENCY_ITEM_IDS
    }
    ids.update(item.item_id for item in materials if is_world_boss_stamina_material(item.item_id))
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
    material_gaps: tuple[str, ...] = (),
) -> ProgressionStaminaResult:
    """按冻结的产出假设估算体力，不补造缺失的成本或平均掉落。"""

    materials = tuple(materials)
    identification = project_identification_level(hunter_level, effective_level=effective_identification_level)
    stages = add_boss_estimates(
        stages, (item.item_id for item in materials if is_world_boss_stamina_material(item.item_id)),
        identification.effective_level,
    )
    stage_item_ids = {item.item_id for stage in stages for item in stage.yields}
    relevant = tuple(
        material for material in materials
        if material.item_id not in _NON_STAMINA_CURRENCY_ITEM_IDS
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
    result = calculate_isolated_progression_stamina(ProgressionStaminaRequest(
        hunter_level=int(hunter_level),
        effective_identification_level=effective_identification_level,
        requirements=requirements,
        stages=stages,
        conversions=conversion_edges(item_ids),
    ))
    return with_material_gaps(result, material_gaps)


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
    """仅排除已知非体力来源；Boss 未闭合掉落交给求解器保留未知。"""

    normalized = str(item_id)
    return normalized == "Fons" or normalized.startswith("OrdinaryMonMaterial_")


__all__ = [
    "calculate_stamina_result",
    "is_non_stamina_source_material",
    "is_world_boss_stamina_material",
    "normalize_owned_quantities",
    "stamina_material_ids",
]
