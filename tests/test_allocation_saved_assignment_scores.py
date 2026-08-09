"""Cover per-item scores persisted by the legacy allocation page."""

from types import SimpleNamespace

from src.features.allocation.runner import _plan_assignment_scores
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
