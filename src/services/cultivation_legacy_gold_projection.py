# 将已核对旧发行数据集的方斯误归并修正为甲硬币的只读投影。
"""Narrow transitional projection for released datasets built before the gold fix."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from src.domain.progression_stamina import FarmingStage, MaterialYield
from src.services.character_progression_requirements import CharacterMaterialRequirement


_LEGACY_GOLD_DATASETS = frozenset({
    ("cn_retail_20260924_9030b45b", 48),
    ("cn_retail_reference_20260924_9030b45b", 47),
})


def has_legacy_gold_alias(source: object) -> bool:
    identity = getattr(source, "progression_dataset_identity", None)
    return callable(identity) and identity() in _LEGACY_GOLD_DATASETS


def correct_legacy_costs(
    requirements: tuple[CharacterMaterialRequirement, ...],
) -> tuple[CharacterMaterialRequirement, ...]:
    """Fix only costs from the two audited release datasets, never owned data."""

    return tuple(
        CharacterMaterialRequirement(
            "Gold" if item.item_id == "Fons" else item.item_id,
            item.required_quantity,
        )
        for item in requirements
    )


def correct_legacy_stages(stages: tuple[FarmingStage, ...]) -> tuple[FarmingStage, ...]:
    """Repair the old drop closure while retaining complete stage yield bundles."""

    corrected: list[FarmingStage] = []
    for stage in stages:
        if stage.stamina_cost <= 0:
            corrected.append(stage)
            continue
        totals: dict[str, int] = defaultdict(int)
        for item in stage.yields:
            totals["Gold" if item.item_id == "Fons" else item.item_id] += item.quantity
        corrected.append(replace(
            stage,
            yields=tuple(MaterialYield(item_id, quantity) for item_id, quantity in totals.items()),
        ))
    return tuple(corrected)


__all__ = ["has_legacy_gold_alias", "correct_legacy_costs", "correct_legacy_stages"]
