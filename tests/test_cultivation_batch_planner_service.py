# 测试多角色养成合并、共享已有材料、体力去重与输入门禁。
from __future__ import annotations

import pytest

from src.domain.progression_stamina import FarmingStage, MaterialYield, StaminaPlanStatus
from src.features.toolbox.cultivation_owned_materials import visible_materials
from src.services.character_progression_requirements import MaterialSummaryStatus
from src.services.cultivation_batch_planner_service import (
    CultivationBatchPlannerService,
    CultivationBatchRequest,
    CultivationTargetDraft,
)
from src.services.cultivation_planner_service import (
    CultivationMaterial,
    CultivationPlan,
    CultivationRequest,
    CultivationSection,
)
from src.services.cultivation_stamina_planner import (
    calculate_stamina_result,
    stamina_material_ids,
)


def _request(character_id: int) -> CultivationRequest:
    return CultivationRequest(character_id, 1, 0, 80, 6, ())


def _target(character_id: int) -> CultivationTargetDraft:
    return CultivationTargetDraft(
        f"line-{character_id}",
        character_id,
        _request(character_id),
    )


def _batch(
    *targets: CultivationTargetDraft,
    owned: tuple[tuple[str, int], ...] = (),
) -> CultivationBatchRequest:
    return CultivationBatchRequest(
        "account",
        7,
        "dataset",
        60,
        7,
        tuple(targets),
        owned,
    )


class _SingleService:
    def __init__(self, materials_by_character=None):
        self.materials_by_character = materials_by_character or {}

    def calculate(self, request):
        materials = self.materials_by_character.get(
            request.character_id,
            (CultivationMaterial("a", "材料 A", 1),),
        )
        return CultivationPlan(
            f"角色 {request.character_id}",
            MaterialSummaryStatus.COMPLETE,
            (CultivationSection("角色升级", materials),),
            materials,
            0,
            0,
            (),
            (),
        )

    def load_farming_stages(self):
        return (FarmingStage(
            "shared-stage",
            "共享材料副本",
            1,
            0,
            40,
            (MaterialYield("a", 1), MaterialYield("b", 1)),
            "test",
        ),)


def test_batch_combines_multi_material_stage_and_reports_saved_stamina() -> None:
    service = CultivationBatchPlannerService(_SingleService({
        1: (CultivationMaterial("a", "材料 A", 1),),
        2: (CultivationMaterial("b", "材料 B", 1),),
    }))

    result = service.calculate(_batch(_target(1), _target(2)))

    assert [(item.item_id, item.quantity) for item in result.merged_totals] == [
        ("a", 1),
        ("b", 1),
    ]
    assert result.combined_stamina.total_stamina == 40
    assert result.separate_stamina_total == 80
    assert result.saved_stamina == 40
    assert result.combined_stamina.runs[0].runs == 1
    assert result.stamina_item_ids == frozenset({"a", "b", "Gold"})


def test_stamina_material_ids_keep_gold_and_require_paid_yields_for_other_items() -> None:
    stages = (
        FarmingStage("paid", "付费副本", 1, 0, 40,
                     (MaterialYield("paid-item", 2), MaterialYield("empty", 0),
                      MaterialYield("Fons", 100), MaterialYield("Gold", 100))),
        FarmingStage("free", "无体力来源", 1, 0, 0,
                     (MaterialYield("free-item", 3),)),
    )

    assert stamina_material_ids(stages) == frozenset({"paid-item", "Gold"})


def test_gold_uses_paid_stage_yield_with_owned_quantity_and_shared_drops() -> None:
    stages = (FarmingStage(
        "paid", "甲硬币副本", 1, 0, 40,
        (MaterialYield("Gold", 50), MaterialYield("paid-item", 1)),
    ),)
    result = calculate_stamina_result(
        (CultivationMaterial("Gold", "甲硬币", 125),
         CultivationMaterial("paid-item", "材料", 1)),
        {"Gold": 25},
        stages,
        hunter_level=60,
        effective_identification_level=7,
    )

    assert result.status is StaminaPlanStatus.COMPLETE
    assert result.total_stamina == 80
    assert result.runs[0].runs == 2
    assert {item.item_id: item.deficit_quantity for item in result.deficits} == {
        "Gold": 100, "paid-item": 1,
    }
    assert visible_materials(
        (CultivationMaterial("Gold", "甲硬币", 125),
         CultivationMaterial("Fons", "方斯", 100)),
        "stamina", stamina_material_ids(stages),
    ) == (CultivationMaterial("Gold", "甲硬币", 125),)


def test_gold_without_verified_paid_yield_keeps_an_explicit_gap() -> None:
    result = calculate_stamina_result(
        (CultivationMaterial("Gold", "甲硬币", 100),),
        {},
        (FarmingStage("paid", "其他副本", 1, 0, 40, (MaterialYield("other", 1),)),),
        hunter_level=60,
        effective_identification_level=7,
    )

    assert result.status is StaminaPlanStatus.UNAVAILABLE
    assert result.unresolved_item_ids == ("Gold",)
    assert result.total_stamina is None
    assert "Gold" in stamina_material_ids(())
    assert visible_materials(
        (CultivationMaterial("Gold", "甲硬币", 100),),
        "stamina", stamina_material_ids(()),
    ) == (CultivationMaterial("Gold", "甲硬币", 100),)


def test_fons_does_not_increase_stamina_even_when_stage_drops_it() -> None:
    stages = (FarmingStage(
        "paid", "材料副本", 1, 0, 40,
        (MaterialYield("paid-item", 1), MaterialYield("Fons", 10)),
    ),)
    result = calculate_stamina_result(
        (CultivationMaterial("paid-item", "材料", 1),
         CultivationMaterial("Fons", "方斯", 1_000)),
        {},
        stages,
        hunter_level=60,
        effective_identification_level=7,
    )

    assert result.total_stamina == 40
    assert {item.item_id for item in result.deficits} == {"paid-item"}


def test_owned_material_is_allocated_once_in_page_order_and_source_ledger() -> None:
    service = CultivationBatchPlannerService(_SingleService())

    result = service.calculate(_batch(
        _target(1),
        _target(2),
        owned=(("a", 1),),
    ))

    assert result.target_plans[0].owned_allocation == (("a", 1),)
    assert result.target_plans[0].remaining_totals == ()
    assert result.target_plans[1].owned_allocation == ()
    assert result.target_plans[1].remaining_totals[0].quantity == 1
    assert [row.allocated_owned for row in result.source_ledger] == [1, 0]
    assert result.remaining_totals[0].quantity == 1


@pytest.mark.parametrize("target_count", [1, 5, 8, 12])
def test_batch_accepts_any_positive_unique_target_count(target_count: int) -> None:
    service = CultivationBatchPlannerService(_SingleService())

    result = service.calculate(_batch(*(
        _target(index) for index in range(1, target_count + 1)
    )))

    assert len(result.target_plans) == target_count
    assert result.merged_totals[0].quantity == target_count


def test_batch_rejects_zero_and_duplicate_targets() -> None:
    service = CultivationBatchPlannerService(_SingleService())

    with pytest.raises(ValueError, match="至少添加"):
        service.calculate(_batch())
    with pytest.raises(ValueError, match="只能加入一次"):
        service.calculate(_batch(_target(1), CultivationTargetDraft(
            "another-line", 1, _request(1)
        )))


def test_unclassified_drop_remains_partial_while_monster_drop_is_ignored() -> None:
    service = CultivationBatchPlannerService(_SingleService({
        1: (
            CultivationMaterial("a", "材料 A", 1),
            CultivationMaterial("unknown", "未知材料", 1),
            CultivationMaterial("OrdinaryMonMaterial_01_lv1", "刷怪材料", 3),
        ),
    }))

    result = service.calculate(_batch(_target(1)))

    assert result.combined_stamina.total_stamina is None
    assert result.combined_stamina.known_stamina == 40
    assert result.combined_stamina.unresolved_item_ids == ("unknown",)


def test_unknown_only_batch_preserves_unavailable_stamina() -> None:
    service = CultivationBatchPlannerService(_SingleService({
        1: (CultivationMaterial("unknown", "未知材料", 1),),
    }))

    result = service.calculate(_batch(_target(1)))

    assert result.combined_stamina.total_stamina is None
    assert result.combined_stamina.known_stamina == 0
    assert result.combined_stamina.unresolved_item_ids == ("unknown",)
