# 验证计算结果保存时沿用既有装备评分。
"""Cover per-item scores persisted by the legacy allocation page."""

from types import SimpleNamespace

from src.services.allocation.slot_comparisons import selected_legacy_comparison_diffs
from src.services.equipment.weighted_loadout_comparison_service import WeightedLoadoutComparison
from src.features.allocation.runner import (
    _plan_assignment_scores,
    _plan_changed_uids,
)
from src.services.allocation.plan_save import legacy_plan_tape_main_values
from src.services.allocation.weighted_save import weighted_option_tape_main_values
from src.optimizer.contracts import (
    PLAN_ASSIGNED_EXTRA_DRIVES,
    PLAN_ASSIGNED_SET_DRIVES,
    DIFF_CHANGED,
    DIFF_ADDED_UIDS,
    PLAN_ASSIGNED_TAPE,
    PLAN_CHANGED_UIDS,
)


def test_plan_assignment_scores_include_every_selected_slot() -> None:
    role_name = "测试角色"
    plan = {
        PLAN_ASSIGNED_TAPE: SimpleNamespace(
            uid="nte-core-8-80",
            role_scores={role_name: 84.0},
        ),
        PLAN_ASSIGNED_SET_DRIVES: [
            SimpleNamespace(
                uid="nte-module-1-10",
                role_scores={role_name: 21.43},
            ),
        ],
        PLAN_ASSIGNED_EXTRA_DRIVES: [
            SimpleNamespace(
                uid="nte-module-2-20",
                role_scores={role_name: 16.0},
            ),
        ],
    }

    assert _plan_assignment_scores(role_name, plan) == {
        "nte-core-8-80": 84.0,
        "nte-module-1-10": 21.43,
        "nte-module-2-20": 16.0,
    }


def test_plan_assignment_scores_supports_dictionary_result_items() -> None:
    role_name = "测试角色"
    plan = {
        PLAN_ASSIGNED_TAPE: {
            "uid": "nte-core-8-80",
            "role_scores": {role_name: 84.0},
        },
        PLAN_ASSIGNED_SET_DRIVES: [{
            "uid": "nte-module-1-10",
            "role_scores": {role_name: 21.43},
        }],
        PLAN_ASSIGNED_EXTRA_DRIVES: [{
            "uid": "nte-module-2-20",
            "role_scores": {role_name: 16.0},
        }],
    }

    assert _plan_assignment_scores(role_name, plan) == {
        "nte-core-8-80": 84.0,
        "nte-module-1-10": 21.43,
        "nte-module-2-20": 16.0,
    }


def test_plan_tape_main_value_is_frozen_for_saved_loadouts() -> None:
    plan = {
        PLAN_ASSIGNED_TAPE: SimpleNamespace(uid="nte-core-8-80", main_value=37.5),
    }

    assert legacy_plan_tape_main_values(plan) == {"nte-core-8-80": 37.5}


def test_weighted_plan_tape_main_value_is_frozen_from_the_calculation_context() -> None:
    candidate = SimpleNamespace(
        uid=(8, 80),
        main_stats=(SimpleNamespace(value=0.375, percent=True),),
    )
    context = SimpleNamespace(candidates=(candidate,))
    option = SimpleNamespace(
        assignments=(SimpleNamespace(kind="core", virtual=False, uid=(8, 80)),),
    )

    assert weighted_option_tape_main_values(context, option) == {"nte-core-8-80": 37.5}


def test_first_save_to_an_empty_slot_has_no_changed_equipment_markers() -> None:
    plan = {
        PLAN_CHANGED_UIDS: {"nte-core-8-80", "nte-module-1-10"},
        PLAN_ASSIGNED_TAPE: {
            "uid": "nte-core-8-80",
            "is_changed": True,
        },
        PLAN_ASSIGNED_SET_DRIVES: [{
            "uid": "nte-module-1-10",
            "is_changed": True,
        }],
    }

    assert _plan_changed_uids(plan, {DIFF_CHANGED: False}) == set()


def test_replacing_a_saved_slot_preserves_real_changed_equipment_markers() -> None:
    plan = {
        PLAN_CHANGED_UIDS: {"nte-core-8-80"},
        PLAN_ASSIGNED_TAPE: {
            "uid": "nte-core-8-80",
            "is_changed": True,
        },
    }

    assert _plan_changed_uids(plan, {DIFF_CHANGED: True}) == {"nte-core-8-80"}


def _comparison(slot_id, old_items=()):
    return WeightedLoadoutComparison(
        slot_id=slot_id, slot_name="测试", slot_key="primary", old_items=old_items,
        diff={"comparison_version": 1, "score_basis": "calculation_weights",
              "baseline_slot_id": slot_id, "baseline_plan_id": 7 if old_items else None},
    )


def test_selected_slot_diff_does_not_compare_against_another_slot() -> None:
    rows = (_comparison(1, ({"uid": "nte-core-1-10", "type": "tape", "score": 40},)), _comparison(2))
    plans = {"早雾": {"valid": True, "assigned_tape": {"uid": "nte-core-2-20"}}}
    result = selected_legacy_comparison_diffs({"早雾": rows}, plans, {"早雾": (1003, 2)})
    assert result["早雾"][DIFF_CHANGED] is False
    assert result["早雾"][DIFF_ADDED_UIDS] == []


def test_selected_slot_diff_uses_the_frozen_selected_slot_as_its_only_baseline() -> None:
    rows = (_comparison(1), _comparison(2, ({"uid": "nte-core-1-10", "type": "tape", "score": 40},)))
    plans = {"早雾": {"valid": True, "assigned_tape": {"uid": "nte-core-2-20"}}}
    result = selected_legacy_comparison_diffs({"早雾": rows}, plans, {"早雾": (1003, 2)})
    assert result["早雾"][DIFF_CHANGED] is True
    assert result["早雾"][DIFF_ADDED_UIDS] == ["nte-core-2-20"]
    assert result["早雾"]["baseline_plan_id"] == 7


def test_selected_slot_diff_keeps_old_tape_main_value_for_summary() -> None:
    old = {"uid": "nte-core-1-10", "type": "tape", "score": 40, "main_value": 30.0}
    rows = (_comparison(2, (old,)),)
    plans = {"早雾": {"valid": True, "assigned_tape": {"uid": "nte-core-2-20"}}}
    result = selected_legacy_comparison_diffs({"早雾": rows}, plans, {"早雾": (1003, 2)})
    assert result["早雾"]["removed"][0]["main_value"] == 30.0
