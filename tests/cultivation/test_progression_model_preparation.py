# 对照不同材料需求的完整体力结果，验证模型仅复用结构并保持任务隔离与有界驻留。
from dataclasses import replace

import pytest

from src.domain.progression.stamina import (
    FarmingStage, MaterialRequirement, MaterialYield, ProgressionStaminaRequest,
    calculate_progression_stamina,
)
from src.domain.progression.stamina_models import StaminaModelPreparation
from src.integrations.progression.solver_process import StaminaSolverSession, calculate_isolated_progression_stamina

NTE_TEST_TIER = "core"


def request(quantity=10, owned=0, *, conversion=False):
    low, high = "SkillUpMaterial_01_lv1", "SkillUpMaterial_01_lv2"
    return ProgressionStaminaRequest(60, (MaterialRequirement(high if conversion else low, quantity, owned),), (
        FarmingStage("a", "A", 1, 0, 20, (MaterialYield(low, 2),)),
        FarmingStage("b", "B", 1, 0, 30, (MaterialYield(low, 4),)),
    ), conversions=((low, high),) if conversion else ())


def test_distinct_targets_reuse_preparation_not_results_and_keep_two_stage_ties():
    for conversion in (False, True):
        prepared = StaminaModelPreparation(minimum_coefficients=0)
        for quantity in range(1, 65):
            value = request(quantity, quantity % 3, conversion=conversion)
            assert calculate_progression_stamina(value, model_preparation=prepared) == calculate_progression_stamina(value)
        assert prepared.build_count == 1
        assert prepared.hit_count > 50


def test_changed_yield_cost_order_ids_and_conversion_do_not_reuse_old_matrix():
    prepared = StaminaModelPreparation(minimum_coefficients=0)
    first = request()
    variants = (first, replace(first, stages=first.stages[::-1]),
        replace(first, stages=(replace(first.stages[0], stamina_cost=21), *first.stages[1:])),
        replace(first, stages=(replace(first.stages[0], yields=(MaterialYield("SkillUpMaterial_01_lv1", 2.5),)), *first.stages[1:])),
        request(conversion=True))
    for value in variants:
        assert calculate_progression_stamina(value, model_preparation=prepared) == calculate_progression_stamina(value)
    assert prepared.build_count == len(variants)
    other = StaminaModelPreparation(minimum_coefficients=0)
    calculate_progression_stamina(first, model_preparation=other)
    assert other.build_count == 1 and other.hit_count == 0


def test_model_memory_and_entry_caps_do_not_change_exact_result():
    for prepared in (StaminaModelPreparation(maximum_entries=1, minimum_coefficients=0), StaminaModelPreparation(maximum_bytes=1, minimum_coefficients=0)):
        first = request()
        for quantity in range(1, 8):
            value = replace(first, stages=(replace(first.stages[0], stamina_cost=quantity + 10), *first.stages[1:]))
            assert calculate_progression_stamina(value, model_preparation=prepared) == calculate_progression_stamina(value)
            assert len(prepared._models) <= 1
            assert prepared.size_bytes <= prepared.maximum_bytes


def test_reused_structure_reports_new_stage_labels_and_still_validates_new_requirements():
    owner = StaminaModelPreparation(minimum_coefficients=0)
    value = request()
    calculate_progression_stamina(value, model_preparation=owner)
    renamed = replace(value, stages=tuple(replace(stage, stage_id=stage.stage_id + "_new", label="新来源", source="new")
        for stage in value.stages))
    result = calculate_progression_stamina(renamed, model_preparation=owner)
    assert result == calculate_progression_stamina(renamed)
    assert owner.build_count == 1
    assert all(run.label == "新来源" and run.source == "new" for run in result.runs)
    with pytest.raises(ValueError, match="不能为负数"):
        calculate_progression_stamina(replace(value, requirements=(MaterialRequirement("invalid", -1),)), model_preparation=owner)


def test_real_helper_session_distinct_requests_equal_unprepared_results():
    from src.integrations.progression import solver_process
    session = StaminaSolverSession()
    try:
        for quantity in (1, 7, 40):
            value = request(quantity, conversion=True)
            assert calculate_isolated_progression_stamina(value, session=session) == calculate_progression_stamina(value)
        assert calculate_isolated_progression_stamina(request(7), session=StaminaSolverSession()) == calculate_progression_stamina(request(7))
    finally:
        solver_process._shutdown()


def test_small_models_keep_lightweight_preparation_without_persistent_keys():
    owner = StaminaModelPreparation()
    for quantity in range(1, 9):
        value = request(quantity)
        assert calculate_progression_stamina(value, model_preparation=owner) == calculate_progression_stamina(value)
    assert owner.hit_count == 0 and not owner._models and owner.size_bytes == 0
