# 验证世界 Boss 突破材料的体力来源、未知掉落及单人多人展示口径。
from pathlib import Path
from unittest.mock import patch

import pytest

from src.domain.progression.stamina import (
    FarmingStage, MaterialYield, StaminaPlanStatus, calculate_progression_stamina,
)
from src.features.toolbox.cultivation.owned_materials import visible_materials
from src.features.toolbox.cultivation.stamina_ui import stamina_summary_text
from src.services.progression.batch_planner import (
    CultivationBatchPlannerService, CultivationBatchRequest, CultivationTargetDraft,
)
from src.services.progression.character_requirements import MaterialSummaryStatus
from src.services.progression.models import (
    CultivationMaterial, CultivationPlan, CultivationRequest, CultivationSection,
)
from src.services.progression.planner_service import CultivationPlannerService
from src.services.progression.stamina_planner import calculate_stamina_result, stamina_material_ids


BOSS = "Worldboss_material_04"
MATERIALS = (
    CultivationMaterial(BOSS, "Boss 突破材料", 5),
    CultivationMaterial("Gold", "养成货币", 10),
    CultivationMaterial("OrdinaryMonMaterial_01_lv1", "普通怪材料", 3),
    CultivationMaterial("Fons", "非体力货币", 1),
)
STAGES = (FarmingStage("coin", "货币副本", 1, 0, 40, (MaterialYield("Gold", 10),)),)


class _Planner(CultivationPlannerService):
    def __init__(self):
        # 仅使用本例冻结输入，不读取账号库、解包目录或运行时静态文件。
        super().__init__(static_database_path=Path("unused-static"), user_database_path=Path("unused-user"))

    def calculate(self, request, **_kwargs):
        return CultivationPlan("角色", MaterialSummaryStatus.COMPLETE,
                               (CultivationSection("角色突破", MATERIALS),),
                               MATERIALS, 0, 0, (1,), ())

    def load_farming_stages(self):
        return STAGES


@pytest.fixture(autouse=True)
def _in_process_solver():
    # 来源/展示测试不需要启动 SciPy 子进程，仍执行正式纯计算入口。
    with patch("src.services.progression.stamina_planner.calculate_isolated_progression_stamina",
               side_effect=lambda request, **_kwargs: calculate_progression_stamina(request)):
        yield


@pytest.mark.parametrize("ordinal", range(1, 8))
def test_reviewed_boss_material_is_visible_without_deterministic_yield(ordinal):
    material = CultivationMaterial(f"Worldboss_material_{ordinal:02}", "突破材料", 1)
    ids = stamina_material_ids((), materials=(material,))
    assert visible_materials((material,), "stamina", ids) == (material,)


def test_unreviewed_identity_does_not_gain_a_paid_source_by_prefix():
    item = CultivationMaterial("Worldboss_material_999", "来源未核实", 1)
    assert item.item_id not in stamina_material_ids((), materials=(item,))


def test_single_prepare_calculate_and_summary_preserve_unknown_total():
    service = _Planner()
    prepared = service.prepare(CultivationRequest(1, 1, 0, 20, 1, ()))
    stamina = service.calculate_stamina(prepared.plan, owned_quantities={}, hunter_level=20,
                                       effective_identification_level=2, farming_stages=prepared.farming_stages)
    assert BOSS in prepared.stamina_item_ids == stamina.stamina_item_ids
    assert {m.item_id for m in visible_materials(MATERIALS, "stamina", stamina.stamina_item_ids)} == {BOSS, "Gold"}
    assert stamina.total.status == StaminaPlanStatus.PARTIAL
    assert stamina.total.known_stamina == 40
    assert stamina.total.total_stamina is None
    assert stamina.total.unresolved_item_ids == (BOSS,)
    assert "总体力未知" in stamina_summary_text(stamina.total)


def test_unknown_yield_with_owned_materials_does_not_guess_runs():
    unknown = calculate_stamina_result((MATERIALS[0],), {}, (), hunter_level=20,
                                       effective_identification_level=2)
    assert unknown.status == StaminaPlanStatus.UNAVAILABLE
    assert unknown.total_stamina is None and not unknown.runs
    assert unknown.gaps == ("material_yield_unavailable",)
    covered = calculate_stamina_result((MATERIALS[0],), {BOSS: 5}, (), hunter_level=20,
                                       effective_identification_level=2)
    assert covered.total_stamina == 0 and not covered.unresolved_item_ids


def test_verified_explicit_stage_still_uses_shared_drop_and_owned_quantity():
    stage = FarmingStage("verified", "已核实档位夹具", 1, 0, 60,
                         (MaterialYield(BOSS, 4), MaterialYield("Gold", 10)))
    result = calculate_stamina_result(MATERIALS, {BOSS: 1}, (stage,), hunter_level=20,
                                      effective_identification_level=2)
    assert result.total_stamina == 60 and result.runs[0].runs == 1


def test_batch_prepare_and_calculate_keep_source_and_allocate_owned_once():
    service = CultivationBatchPlannerService(_Planner())
    targets = tuple(CultivationTargetDraft(str(i), i, CultivationRequest(i, 1, 0, 20, 1, ()))
                    for i in (1, 2))
    request = CultivationBatchRequest("account", 1, "dataset", 20, 2, targets, ((BOSS, 5),))
    prepared = service.prepare(request)
    result = service.calculate(request, preparation=prepared)
    assert BOSS in prepared.stamina_item_ids == result.stamina_item_ids
    assert [p.stamina.total.total_stamina for p in result.target_plans] == [40, None]
    assert result.combined_stamina.total_stamina is None
    assert result.combined_stamina.known_stamina == 80
    assert result.combined_stamina.unresolved_item_ids == (BOSS,)
    assert result.saved_stamina is None
