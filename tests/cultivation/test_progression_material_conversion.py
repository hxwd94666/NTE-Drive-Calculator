# 固化三合一材料的库存、掉落及经验材料隔离行为。
"""Public behavior tests for conversion-aware cultivation planning."""

from __future__ import annotations

from src.domain.progression.materials import allocate_owned, conversion_edges
from src.domain.progression.stamina import FarmingStage, MaterialYield
from src.services.progression.character_requirements import MaterialSummaryStatus
from src.services.progression.batch_planner import (
    CultivationBatchPlannerService,
    CultivationBatchRequest,
    CultivationTargetDraft,
)
from src.services.progression.planner_service import (
    CultivationMaterial, CultivationPlan, CultivationRequest, CultivationSection,
)
from src.services.progression.stamina_planner import (
    calculate_stamina_result,
    stamina_material_ids,
)


def test_stamina_batch_reuses_only_identical_frozen_solver_requests(monkeypatch):
    from types import SimpleNamespace
    from src.services.progression import stamina_planner as module
    session = module.StaminaPlanningSession()
    actual = module.calculate_isolated_progression_stamina
    calls = []
    def solve(request, **kwargs):
        calls.append(request)
        return actual(request, **kwargs)
    monkeypatch.setattr(module, "calculate_isolated_progression_stamina", solve)
    materials = (SimpleNamespace(item_id="Gold", quantity=12),)
    def calculate(owned, gaps=(), owner=session):
        return calculate_stamina_result(materials, owned, (_stage("Gold", 2),),
            hunter_level=60, effective_identification_level=7, material_gaps=gaps, session=owner)
    first = calculate({})
    assert calculate({}) == first and len(calls) == 1
    assert calculate({"Gold": 12}).total_stamina == 0 and len(calls) == 2
    assert calculate({}, ("missing_source",)).gaps != first.gaps and len(calls) == 2
    assert calculate({}, owner=module.StaminaPlanningSession()) == first and len(calls) == 3


def test_experience_reuse_preserves_minimum_overflow_count_and_stable_order():
    from concurrent.futures import ThreadPoolExecutor
    from src.domain.progression.materials import ExperienceMaterial, least_waste_experience_materials
    materials = (ExperienceMaterial("b", 18), ExperienceMaterial("a", 18), ExperienceMaterial("c", 7))
    # 独立穷举小目标的数量组合；先溢出、再件数，相同面额保留 ID 稳定顺序。
    def expected(required):
        options = [(18*a+7*c-required, a+c, a, c) for a in range(25) for c in range(65)
                   if 18*a+7*c >= required]
        overflow, _, a, c = min(options)
        return tuple((key, count) for key, count in (("a", a), ("c", c)) if count), overflow
    def calculate(required):
        quantities, overflow = least_waste_experience_materials(materials, required)
        return tuple((row.item_id, row.quantity) for row in quantities), overflow
    targets = tuple(range(1, 400, 3)) + (18, 7, 90, 1, 399)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert tuple(pool.map(calculate, targets)) == tuple(expected(n) for n in targets)
    changed, _ = least_waste_experience_materials((ExperienceMaterial("new", 17),), 19)
    assert [(row.item_id, row.quantity) for row in changed] == [("new", 2)]


def test_stamina_memo_keeps_input_types_and_does_not_cache_failures(monkeypatch):
    from dataclasses import replace
    import pytest
    from src.domain.progression.stamina import MaterialRequirement, ProgressionStaminaRequest
    from src.services.progression import stamina_planner as module
    session = module.StaminaPlanningSession()
    request = ProgressionStaminaRequest(60, (MaterialRequirement("Gold", 1),), (_stage("Gold"),), 7)
    calls = []
    def solve(value, **_kwargs):
        calls.append(value)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return value
    monkeypatch.setattr(module, "calculate_isolated_progression_stamina", solve)
    with pytest.raises(RuntimeError, match="transient"):
        session.solve(request)
    assert session.solve(request) == request and len(calls) == 2
    assert session.solve(request) == request and len(calls) == 2
    # True 与 1 在 Python 中相等，但不允许将类型不同的输入冒充同一冻结请求。
    session.solve(replace(request, requirements=(MaterialRequirement("Gold", True),)))
    assert len(calls) == 3


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
