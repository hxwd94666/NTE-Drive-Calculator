# 验证装配状态按冻结槽位归属，派发、确认、部分结果和停止互不冒充。
from dataclasses import FrozenInstanceError

import pytest

from src.domain.equipment.assembly.assembly_progress import AssemblyProgress, AssemblyTarget
from src.services.equipment.assembly.assembly_progress import reconcile_fast_result

NTE_TEST_TIER = "core"


def progress():
    return AssemblyProgress(
        (AssemblyTarget(11, 1001, "角色甲", "主力", 230.0), AssemblyTarget(12, 1002, "角色乙", "备用", None))
    )


def test_dispatch_is_not_verification_and_keeps_frozen_score():
    model = progress()
    model.update(11, "waiting", dispatched=True)
    assert model.dispatched == 1 and model.confirmed == 0
    assert model.rows[0].target.score == 230.0
    with pytest.raises(FrozenInstanceError):
        model.rows[0].target.score = 999


def test_partial_scope_never_counts_as_complete_loadout():
    model = progress()
    model.update(11, "partial", dispatched=True, verified=True, scope="subset")
    assert model.confirmed == 0 and model.partial == 1


def test_stop_preserves_confirmed_and_distinguishes_unknown_from_not_started():
    model = progress()
    model.update(11, "confirmed", dispatched=True, verified=True, scope="full")
    model.finish_pending(stopped=True)
    assert [row.state for row in model.rows] == ["confirmed", "not_started"]
    assert model.count_state("pending", "not_started") == 1
    other = progress()
    other.update(11, "operating", dispatched=True)
    other.finish_pending(stopped=True)
    assert [row.state for row in other.rows] == ["unknown", "not_started"]
    assert other.count_state("unknown") == 1


def test_late_or_foreign_slot_does_not_change_current_rows():
    model = progress()
    before = model.rows
    model.update(999, "confirmed", dispatched=True, verified=True)
    assert model.rows == before


def test_target_identity_must_be_unique():
    with pytest.raises(ValueError):
        AssemblyProgress((AssemblyTarget(11, 1, "甲", "主力", 1), AssemblyTarget(11, 2, "乙", "主力", 2)))


def test_scoped_fragment_is_unknown_but_new_full_observation_can_confirm():
    model = progress()
    report = {"applied": [{"role_name": "角色甲", "verified": True, "scoped_verified": True}]}
    reconcile_fast_result(model, report)
    assert model.confirmed == 0 and model.rows[0].state == "unknown"
    report["applied"][0]["full_snapshot_verified"] = True
    reconcile_fast_result(model, report)
    assert model.confirmed == 1 and model.rows[0].scope == "full"


def test_virtual_and_missing_counts_stay_on_their_frozen_role():
    model = progress()
    reconcile_fast_result(
        model,
        {
            "applied": [
                {
                    "role_name": "角色甲",
                    "verified": True,
                    "skipped_assignments": [{"reason": "virtual"}, {"reason": "missing"}],
                }
            ]
        },
    )
    assert model.partial == 1 and model.confirmed == 0
    assert model.rows[0].skip_counts == (1, 1)
    assert model.rows[1].target.score is None
