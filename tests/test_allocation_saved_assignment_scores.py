# 验证计算结果保存时沿用既有装备评分。
"""Cover per-item scores persisted by the legacy allocation page."""

from types import SimpleNamespace

from src.features.weighted_allocation.runner import _option_tape_main_values

from src.features.allocation.slot_plan_diff import selected_slot_plan_diff
from src.features.allocation.runner import (
    _plan_assignment_scores,
    _plan_changed_uids,
    _plan_tape_main_values,
)
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


class _SlotDiffDao:
    def __init__(self, slots):
        self._slots = slots

    def get_loadout_slot(self, slot_id):
        return self._slots.get(slot_id)


def test_selected_slot_diff_does_not_compare_against_another_slot() -> None:
    first_plan = {
        "assignments": [{"kind": "core", "uid_slot": 1, "uid_serial": 10}],
    }
    dao = _SlotDiffDao({
        1: {"character_id": 1003, "current_plan": first_plan},
        2: {"character_id": 1003, "current_plan": None},
    })
    final_plan = {
        "早雾": {
            "valid": True,
            "assigned_tape": {"uid": "nte-core-2-20"},
        }
    }

    result = selected_slot_plan_diff(dao, final_plan, {"早雾": (1003, 2)})

    assert result["早雾"][DIFF_CHANGED] is False
    assert result["早雾"][DIFF_ADDED_UIDS] == set()


def test_selected_slot_diff_uses_the_selected_slot_as_its_only_baseline() -> None:
    second_plan = {
        "assignments": [{"kind": "core", "uid_slot": 1, "uid_serial": 10}],
    }
    dao = _SlotDiffDao({
        1: {"character_id": 1003, "current_plan": None},
        2: {"character_id": 1003, "current_plan": second_plan},
    })
    final_plan = {
        "早雾": {
            "valid": True,
            "assigned_tape": {"uid": "nte-core-2-20"},
        }
    }

    result = selected_slot_plan_diff(dao, final_plan, {"早雾": (1003, 2)})

    assert result["早雾"][DIFF_CHANGED] is True
    assert result["早雾"][DIFF_ADDED_UIDS] == {"nte-core-2-20"}
