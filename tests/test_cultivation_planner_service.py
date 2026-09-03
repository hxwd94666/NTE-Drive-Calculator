# 验证养成材料计划服务。
"""Behavior tests for account-state-aware character cultivation material planning."""

from __future__ import annotations

from src.services.cultivation_planner_service import (
    CultivationForkTarget,
    CultivationRequest,
    CultivationRole,
    _canonical_fork_item_id,
    _breakthrough_requirements,
    _deduplicate_roles,
    _validate_state,
)
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


def test_role_picker_deduplicates_by_visible_role_name_in_catalog_order() -> None:
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
