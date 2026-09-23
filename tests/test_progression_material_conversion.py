# 固化三合一材料的库存、掉落及经验材料隔离行为。
"""Public behavior tests for conversion-aware cultivation planning."""

from __future__ import annotations

from src.domain.progression_material_conversion import allocate_owned, conversion_edges
from src.domain.progression_stamina import FarmingStage, MaterialYield
from src.services.character_progression_requirements import MaterialSummaryStatus
from src.services.cultivation_batch_planner_service import (
    CultivationBatchPlannerService,
    CultivationBatchRequest,
    CultivationTargetDraft,
)
from src.services.cultivation_planner_service import (
    CultivationMaterial, CultivationPlan, CultivationRequest, CultivationSection,
)
from src.services.cultivation_stamina_planner import (
    calculate_stamina_result,
    stamina_material_ids,
)


def _stage(item_id: str, quantity: int = 1) -> FarmingStage:
    return FarmingStage(
        "fixture", "材料副本", 1, 0, 40,
        (MaterialYield(item_id, quantity),), "test_fixture",
    )


def test_owned_low_tier_crafts_high_tier_without_double_spending() -> None:
    high = "SkillUpMaterial_01_lv3"
    mid = "SkillUpMaterial_01_lv2"
    low = "SkillUpMaterial_01_lv1"
    stock = {low: 9, mid: 2}
    allocated = allocate_owned({mid: 1, high: 1}, stock)

    assert allocated == {mid: 1, high: 1}
    assert stock[low] == 3
    assert stock[mid] == 0


def test_low_tier_stage_drops_accumulate_across_runs_for_one_high_tier() -> None:
    low = "SkillUpMaterial_01_lv1"
    high = "SkillUpMaterial_01_lv2"
    result = calculate_stamina_result(
        (CultivationMaterial(high, "高档", 1),), {}, (_stage(low),),
        hunter_level=60, effective_identification_level=7,
    )

    assert result.total_stamina == 120
    assert result.runs[0].runs == 3
    assert high in stamina_material_ids((_stage(low),))


def test_existing_two_low_tier_items_need_one_more_stage_run() -> None:
    low = "SkillUpMaterial_01_lv1"
    high = "SkillUpMaterial_01_lv2"
    result = calculate_stamina_result(
        (CultivationMaterial(high, "高档", 1),), {low: 2}, (_stage(low),),
        hunter_level=60, effective_identification_level=7,
    )

    assert result.total_stamina == 40


def test_character_and_fork_experience_materials_do_not_convert() -> None:
    for family in ("CharacterUpMaterial", "WeaponUpMaterial"):
        assert conversion_edges({f"{family}_lv3": 1}) == ()
        stock = {f"{family}_lv1": 9}
        assert allocate_owned({f"{family}_lv3": 1}, stock) == {
            f"{family}_lv3": 0,
        }
        assert stock[f"{family}_lv1"] == 9


def test_batch_consumes_shared_low_tier_stock_once() -> None:
    high = "SkillUpMaterial_01_lv2"
    low = "SkillUpMaterial_01_lv1"

    class Single:
        def calculate(self, request: CultivationRequest) -> CultivationPlan:
            materials = (CultivationMaterial(high, "高档", 1),)
            return CultivationPlan(
                str(request.character_id), MaterialSummaryStatus.COMPLETE,
                (CultivationSection("技能", materials),), materials, 0, 0, (), (),
            )

        def load_farming_stages(self) -> tuple[FarmingStage, ...]:
            return (_stage(high),)

    drafts = tuple(
        CultivationTargetDraft(str(character_id), character_id,
                               CultivationRequest(character_id, 1, 0, 80, 6, ()))
        for character_id in (1, 2)
    )
    result = CultivationBatchPlannerService(Single()).calculate(
        CultivationBatchRequest("account", 1, "dataset", 60, 7, drafts, ((low, 3),))
    )

    assert [target.remaining_totals[0].quantity if target.remaining_totals else 0
            for target in result.target_plans] == [0, 1]
    assert result.remaining_totals[0].quantity == 1
    assert result.combined_stamina.total_stamina == 40
