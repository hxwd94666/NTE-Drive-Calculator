# 验证养成材料计划服务。
"""Behavior tests for account-state-aware character cultivation material planning."""

from __future__ import annotations

from types import SimpleNamespace

from src.domain.progression_stamina import FarmingStage, MaterialYield
from src.services.character_progression_requirements import MaterialSummaryStatus
from src.services.cultivation_planner_service import (
    CultivationForkTarget,
    CultivationMaterial,
    CultivationPlan,
    CultivationPlannerService,
    CultivationRequest,
    CultivationRole,
    CultivationSection,
    _canonical_fork_item_id,
    _breakthrough_requirements,
    _deduplicate_roles,
    _validate_state,
)
from src.services.fork_progression_requirements import (
    project_fork_level_requirements,
)
from src.services.static_catalog_fork_service import ForkCost
from src.services.static_catalog_character_models import (
    CatalogSource,
    CharacterBreakthroughRequirement,
    CharacterMaterialCost,
)


NTE_TEST_TIER = "core"

_SOURCE = CatalogSource(table_name="test")
_STAGES = (
    CharacterBreakthroughRequirement(0, 20, 0, (), _SOURCE),
    CharacterBreakthroughRequirement(
        1, 30, 1, (CharacterMaterialCost("material-a", 5),), _SOURCE
    ),
    CharacterBreakthroughRequirement(
        2, 40, 1, (CharacterMaterialCost("material-a", 7),), _SOURCE
    ),
)


def test_breakthrough_before_at_cap_includes_current_gate() -> None:
    requirements, included = _breakthrough_requirements(
        _STAGES,
        current_level=20,
        current_stage=0,
        target_level=20,
        target_stage=1,
    )

    assert included == (1,)
    assert requirements[0].item_id == "material-a"
    assert requirements[0].required_quantity == 5


def test_breakthrough_after_at_cap_does_not_charge_same_gate_again() -> None:
    requirements, included = _breakthrough_requirements(
        _STAGES,
        current_level=20,
        current_stage=1,
        target_level=40,
        target_stage=2,
    )

    assert included == (2,)
    assert requirements[0].required_quantity == 7


def test_breakthrough_state_rejects_invalid_level_stage_pair() -> None:
    try:
        _validate_state(20, 2, _STAGES)
    except ValueError as exc:
        assert str(exc) == "角色等级与突破阶段不匹配"
    else:
        raise AssertionError("invalid stage must be rejected")


def test_request_keeps_skill_costs_as_independent_explicit_targets() -> None:
    request = CultivationRequest(
        character_id=1001,
        current_level=20,
        current_breakthrough_stage=1,
        target_level=40,
        target_breakthrough_stage=2,
        skills=(),
    )

    assert request.target_breakthrough_stage == 2
    assert request.skills == ()


def test_fork_gold_cost_uses_the_shared_fons_identity() -> None:
    assert _canonical_fork_item_id("gold") == "Fons"
    assert _canonical_fork_item_id("WeaponBreakMaterial_02_lv1") == "WeaponBreakMaterial_02_lv1"


def test_fork_material_target_only_tracks_level_and_breakthrough() -> None:
    target = CultivationForkTarget("fork-a", 20, 1, 40, 2)

    assert target.target_level == 40
    assert not hasattr(target, "current_refinement_level")


def test_role_picker_deduplicates_by_visible_name_before_alphabetic_sort() -> None:
    roles = _deduplicate_roles((
        CultivationRole(1004, "安魂曲"),
        CultivationRole(1056, "安魂曲"),
        CultivationRole(1046, "零"),
        CultivationRole(1046, "零·重复"),
    ))

    assert roles == (
        CultivationRole(1004, "安魂曲"),
        CultivationRole(1046, "零"),
    )


def test_fork_projection_converts_exp_and_separates_use_from_break_costs() -> None:
    detail = SimpleNamespace(
        growth_levels=(
            SimpleNamespace(level=2, need_exp=500),
            SimpleNamespace(level=3, need_exp=10000),
        ),
        experience_materials=(
            SimpleNamespace(
                item_id="WeaponUpMaterial_lv1",
                experience_value=500,
                costs=(ForkCost("gold", 150, "150"),),
            ),
            SimpleNamespace(
                item_id="WeaponUpMaterial_lv2",
                experience_value=2500,
                costs=(ForkCost("gold", 750, "750"),),
            ),
            SimpleNamespace(
                item_id="WeaponUpMaterial_lv3",
                experience_value=10000,
                costs=(ForkCost("gold", 3000, "3000"),),
            ),
        ),
        breakthroughs=(
            SimpleNamespace(
                stage=1,
                item_costs=(ForkCost("WeaponBreakMaterial_01_lv1", 3, "3"),),
                gold_costs=(ForkCost("gold", 16000, "16000"),),
            ),
        ),
    )

    result = project_fork_level_requirements(
        detail,
        current_level=1,
        current_stage=0,
        target_level=3,
        target_stage=1,
    )

    assert result.required_experience == 10500
    assert result.experience_overflow == 0
    assert {(item.item_id, item.required_quantity) for item in result.experience_materials} == {
        ("WeaponUpMaterial_lv1", 1),
        ("WeaponUpMaterial_lv3", 1),
    }
    assert result.experience_costs[0].item_id == "Fons"
    assert result.experience_costs[0].required_quantity == 3150
    assert result.breakthrough_materials[0].required_quantity == 3
    assert result.breakthrough_costs[0].required_quantity == 16000
    assert result.included_breakthrough_stages == (1,)


def test_stamina_plan_reports_merged_total_and_each_section() -> None:
    class StageDao:
        def __init__(self, _path):
            pass

        def list_progression_farming_stages(self):
            return (FarmingStage(
                "material-stage",
                "材料本 · 鉴别 7",
                1,
                0,
                40,
                (MaterialYield("material-a", 2), MaterialYield("Fons", 10)),
                "test",
            ),)

        def close(self):
            pass

    def material(quantity: int) -> CultivationMaterial:
        return CultivationMaterial("material-a", "测试材料", quantity)

    monster_drop = CultivationMaterial(
        "OrdinaryMonMaterial_02_lv1", "刷怪材料", 9
    )
    fons = CultivationMaterial("Fons", "方斯", 15)
    plan = CultivationPlan(
        "测试角色",
        MaterialSummaryStatus.COMPLETE,
        (
            CultivationSection("角色升级", (material(4),)),
            CultivationSection("角色突破", (material(2), monster_drop, fons)),
        ),
        (material(6), monster_drop, fons),
        0,
        0,
        (),
        (),
    )
    service = CultivationPlannerService(
        static_database_path="static.sqlite3",
        user_database_path="user.sqlite3",
        terminology_dao_factory=StageDao,
    )

    stamina = service.calculate_stamina(
        plan,
        owned_quantities={"material-a": 3},
        hunter_level=60,
        effective_identification_level=7,
    )

    assert stamina.total.total_stamina == 80
    assert stamina.total.unresolved_item_ids == ()
    assert {item.item_id for item in stamina.total.deficits} == {"material-a"}
    assert stamina.stamina_item_ids == frozenset({"material-a"})
    assert [item.result.total_stamina for item in stamina.sections] == [40, 40]
    assert [item.result.deficits[0].owned_quantity for item in stamina.sections] == [3, 0]
