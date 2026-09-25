# 验证已核对旧发行数据集的甲硬币投影不会扩散到其他数据集。
from __future__ import annotations

from src.domain.progression_stamina import FarmingStage, MaterialYield
from src.services.character_progression_requirements import CharacterMaterialRequirement
from src.services.cultivation_legacy_gold_projection import (
    correct_legacy_costs,
    correct_legacy_stages,
    has_legacy_gold_alias,
)


class _Source:
    def __init__(self, identity):
        self.identity = identity

    def progression_dataset_identity(self):
        return self.identity


def test_only_audited_old_dataset_versions_enable_cost_projection() -> None:
    assert has_legacy_gold_alias(_Source(("cn_retail_20260924_9030b45b", 48)))
    assert has_legacy_gold_alias(_Source(("cn_retail_reference_20260924_9030b45b", 47)))
    assert not has_legacy_gold_alias(_Source(("cn_retail_20260924_9030b45b", 49)))
    assert not has_legacy_gold_alias(_Source(("other_dataset", 48)))
    assert not has_legacy_gold_alias(object())


def test_legacy_projection_preserves_other_materials_and_full_stage_bundle() -> None:
    costs = (
        CharacterMaterialRequirement("Fons", 100),
        CharacterMaterialRequirement("material", 2),
    )
    stage = FarmingStage(
        "paid", "正式副本", 1, 0, 40,
        (MaterialYield("Fons", 50), MaterialYield("material", 1)),
    )

    assert [(row.item_id, row.required_quantity) for row in correct_legacy_costs(costs)] == [
        ("Gold", 100), ("material", 2),
    ]
    assert [(row.item_id, row.quantity) for row in correct_legacy_stages((stage,))[0].yields] == [
        ("Gold", 50), ("material", 1),
    ]
    assert stage.yields[0].item_id == "Fons"
    free_stage = FarmingStage(
        "free", "显式方斯来源", 1, 0, 0, (MaterialYield("Fons", 1),),
    )
    assert correct_legacy_stages((free_stage,))[0].yields[0].item_id == "Fons"
