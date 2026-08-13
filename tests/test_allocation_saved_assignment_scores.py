# 验证计算结果保存时沿用既有装备评分。
"""Cover per-item scores persisted by the legacy allocation page."""

from types import SimpleNamespace

from src.features.weighted_allocation.runner import _option_tape_main_values

from src.features.allocation.runner import _plan_assignment_scores, _plan_tape_main_values
from src.optimizer.contracts import (
    PLAN_ASSIGNED_EXTRA_DRIVES,
    PLAN_ASSIGNED_SET_DRIVES,
    PLAN_ASSIGNED_TAPE,
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

    assert _plan_tape_main_values(plan) == {"nte-core-8-80": 37.5}


def test_weighted_plan_tape_main_value_is_frozen_from_the_calculation_context() -> None:
    candidate = SimpleNamespace(
        uid=(8, 80),
        main_stats=(SimpleNamespace(value=0.375, percent=True),),
    )
    context = SimpleNamespace(candidates=(candidate,))
    option = SimpleNamespace(
        assignments=(SimpleNamespace(kind="core", virtual=False, uid=(8, 80)),),
    )

    assert _option_tape_main_values(context, option) == {"nte-core-8-80": 37.5}
