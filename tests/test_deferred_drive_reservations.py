# 验证角色优先同分驱动预留池的一对一回填语义。
from concurrent.futures import CancelledError

import pytest
from src.models.equipment import Drive
from src.optimizer.deferred_drive_reservations import (
    DeferredDriveReservationState,
    DeferredDriveSlot,
)
from src.optimizer.reservation_recovery_support import ProtectedDriveScreenCache
from src.optimizer.role_priority_execution import _choose_group_allocation
from src.optimizer.role_priority_strategy import RolePriorityStrategy


def _drive(uid: str) -> Drive:
    return Drive(
        uid=uid,
        quality="Gold",
        area=1,
        shape_id="X",
        set_name="Set",
        main_stats={"a": 1, "b": 1},
    )


def _slot(key: str, baseline: str, candidates: tuple[str, ...]) -> DeferredDriveSlot:
    return DeferredDriveSlot(
        key=key,
        group_index=0,
        role_name="A",
        slot_type="extra",
        slot_index=0,
        baseline_uid=baseline,
        candidate_uids=candidates,
    )


def test_later_role_can_consume_high_value_tie_and_leave_a_return_drive():
    state = DeferredDriveReservationState()
    a, b = _drive("a"), _drive("b")

    registration = state.register([(_slot("slot", "a", ("a", "b")), [a, b])])

    assert registration.fixed_uids == frozenset()
    assert {drive.uid for drive in registration.exposed_drives} == {"a", "b"}
    assert state.can_consume({"a"})
    assert not state.can_consume({"a", "b"})

    state.commit({"a"})

    assert state.finalize() == {"slot": b}


def test_overlapping_pools_require_one_to_one_completion_not_individual_counts():
    state = DeferredDriveReservationState()
    a, b = _drive("a"), _drive("b")
    state.register([
        (_slot("first", "a", ("a", "b")), [a, b]),
        (_slot("second", "b", ("a", "b")), [a, b]),
    ])

    assert not state.can_consume({"a"})
    assert not state.can_consume({"b"})
    assert {key: drive.uid for key, drive in state.finalize().items()} == {
        "first": "b", "second": "a",
    }


def test_effective_blocker_uses_matching_cardinality_not_membership_only():
    """Only a release that improves the complete matching is protected."""

    state = DeferredDriveReservationState()
    a, b, c = (_drive(uid) for uid in ("a", "b", "c"))
    state.register([
        (_slot("first", "a", ("a", "b")), [a, b]),
        (_slot("second", "b", ("b", "c")), [b, c]),
    ])

    assert state.matching_size_after_consuming({"a", "b"}) == 1
    assert state.effective_blocker_uids({"a", "b"}) == ("a", "b")


def test_effective_blocker_ignores_used_reservation_that_cannot_improve_matching():
    state = DeferredDriveReservationState()
    a, b, c = (_drive(uid) for uid in ("a", "b", "c"))
    state.register([
        (_slot("first", "a", ("a", "b")), [a, b]),
        (_slot("second", "b", ("a", "b")), [a, b]),
    ])

    assert state.matching_size_after_consuming({"a", "c"}) == 1
    assert state.effective_blocker_uids({"a", "c"}) == ("a",)


def test_protected_top_k_refills_from_cached_role_type_ranking():
    drives = [
        _scored_drive("a", {"Later": 30.0}),
        _scored_drive("b", {"Later": 20.0}),
        _scored_drive("c", {"Later": 10.0}),
    ]
    strategy = RolePriorityStrategy({}, {}, {})
    cache = ProtectedDriveScreenCache(strategy, drives, {})

    selected = cache.select(["Later"], {"X"}, 2, {"a"})

    assert [drive.uid for drive in selected] == ["b", "c"]


def test_fixed_peer_removes_its_uid_from_other_reservation_candidates():
    state = DeferredDriveReservationState()
    a, b = _drive("a"), _drive("b")

    registration = state.register([
        (_slot("fixed", "a", ("a",)), [a]),
        (_slot("delayed", "b", ("a", "b")), [a, b]),
    ])

    assert registration.deferred_slot_count == 0
    assert registration.fixed_uids == frozenset({"a", "b"})


def test_strict_priority_defers_equal_drive_for_later_role_with_higher_value():
    drives = [
        _scored_drive("a", {"High": 10.0, "Later": 100.0}),
        _scored_drive("b", {"High": 10.0, "Later": 1.0}),
    ]
    strategy = RolePriorityStrategy(
        {"High": {"default_set": "Set"}, "Later": {"default_set": "Set"}},
        {"Set": {"shapes": []}},
        {
            "High": [{"set_pieces": [], "extra_pieces": ["X"]}],
            "Later": [{"set_pieces": [], "extra_pieces": ["X"]}],
        },
    )

    result = strategy.execute(
        {"drives": [drives[0]], "all_drives": drives, "tapes": {}},
        ["High", "Later"],
        {"High": "Set", "Later": "Set"},
        priority_groups=[["High"], ["Later"]],
    )

    assert result["High"]["assigned_extra_drives"][0].uid == "b"
    assert result["Later"]["assigned_extra_drives"][0].uid == "a"
    assert result["High"]["score"] == 10.0


def test_equal_group_finishes_before_its_tie_is_available_to_later_role():
    drives = [
        _scored_drive("a", {"A": 10.0, "B": 0.0, "Later": 100.0}),
        _scored_drive("b", {"A": 10.0, "B": 0.0, "Later": 1.0}),
        _scored_drive("c", {"A": 0.0, "B": 9.0, "Later": 0.0}, shape="Y"),
    ]
    strategy = RolePriorityStrategy(
        {
            "A": {"default_set": "Set"}, "B": {"default_set": "Set"},
            "Later": {"default_set": "Set"},
        },
        {"Set": {"shapes": []}},
        {
            "A": [{"set_pieces": [], "extra_pieces": ["X"]}],
            "B": [{"set_pieces": [], "extra_pieces": ["Y"]}],
            "Later": [{"set_pieces": [], "extra_pieces": ["X"]}],
        },
    )

    result = strategy.execute(
        {"drives": drives, "all_drives": drives, "tapes": {}},
        ["A", "B", "Later"],
        {"A": "Set", "B": "Set", "Later": "Set"},
        priority_groups=[["A", "B"], ["Later"]],
    )

    assert result["A"]["assigned_extra_drives"][0].uid == "b"
    assert result["B"]["assigned_extra_drives"][0].uid == "c"
    assert result["Later"]["assigned_extra_drives"][0].uid == "a"


def test_critical_rate_difference_keeps_same_score_drive_out_of_reservation():
    drives = [
        _scored_drive("a", {"High": 10.0, "Later": 100.0}),
        Drive(
            uid="b",
            quality="Gold",
            area=1,
            shape_id="X",
            set_name="Set",
            main_stats={"a": 1, "b": 1},
            sub_stats={"暴击率": 5.0},
            role_scores={"High": 10.0, "Later": 1.0},
        ),
    ]
    strategy = RolePriorityStrategy(
        {"High": {"default_set": "Set"}, "Later": {"default_set": "Set"}},
        {"Set": {"shapes": []}},
        {
            "High": [{"set_pieces": [], "extra_pieces": ["X"]}],
            "Later": [{"set_pieces": [], "extra_pieces": ["X"]}],
        },
    )

    result = strategy.execute(
        {"drives": drives, "all_drives": drives, "tapes": {}},
        ["High", "Later"],
        {"High": "Set", "Later": "Set"},
        priority_groups=[["High"], ["Later"]],
    )

    assert result["High"]["assigned_extra_drives"][0].uid == "a"
    assert result["Later"]["assigned_extra_drives"][0].uid == "b"


def test_equal_choices_have_stable_final_uids_when_inventory_order_changes():
    first = _run_equal_choice_allocation(["a", "b"])
    reversed_order = _run_equal_choice_allocation(["b", "a"])

    assert first == reversed_order == {"High": "b", "Later": "a"}


def test_later_reservation_does_not_reexpose_an_earlier_fixed_uid():
    drives = [
        _scored_drive("a", {"First": 10.0, "Second": 10.0, "Later": 100.0}),
        _scored_drive("c", {"First": 1.0, "Second": 10.0, "Later": 1.0}),
    ]
    strategy = RolePriorityStrategy(
        {
            "First": {"default_set": "Set"}, "Second": {"default_set": "Set"},
            "Later": {"default_set": "Set"},
        },
        {"Set": {"shapes": []}},
        {
            "First": [{"set_pieces": [], "extra_pieces": ["X"]}],
            "Second": [{"set_pieces": [], "extra_pieces": ["X"]}],
            "Later": [{"set_pieces": [], "extra_pieces": ["X"]}],
        },
    )

    result = strategy.execute(
        {"drives": drives, "all_drives": drives, "tapes": {}},
        ["First", "Second", "Later"],
        {"First": "Set", "Second": "Set", "Later": "Set"},
        priority_groups=[["First"], ["Second"], ["Later"]],
    )

    assert result["First"]["assigned_extra_drives"][0].uid == "a"
    assert result["Second"]["assigned_extra_drives"][0].uid == "c"
    assert not result["Later"]["valid"]


def test_group_progressively_protects_only_drives_used_by_the_failed_plan():
    """The quick path protects one actual blocker before trying the next plan."""

    drives = [_drive(uid) for uid in ("a", "b", "c")]
    reservations = DeferredDriveReservationState()
    reservations.register([
        (_slot("set", "a", ("a", "c")), drives),
        (_slot("extra", "b", ("b", "c")), drives),
    ])

    result = _choose_group_allocation(
        _ReservationSearchStrategy(),
        ["Later"],
        drives,
        drives,
        {},
        {},
        {},
        {},
        set(),
        {},
        set(),
        reservations,
        1,
    )

    assert result["Later"]["score"] == 10.0
    assert [drive.uid for drive in result["Later"]["assigned_extra_drives"]] == ["c"]


def test_role_priority_combo_limit_is_request_scoped_and_cancellable():
    strategy = RolePriorityStrategy({}, {}, {})
    strategy.configure_execution(combo_limit=2)

    assert list(strategy._iter_bp_combos([["first", "second", "third"]])) == [
        ("first",), ("second",),
    ]

    strategy.configure_execution(combo_limit=3, cancel_check=lambda: True)
    with pytest.raises(CancelledError):
        next(strategy._iter_bp_combos([["first", "second", "third"]]))


def _scored_drive(uid: str, scores: dict[str, float], shape: str = "X") -> Drive:
    return Drive(
        uid=uid,
        quality="Gold",
        area=1,
        shape_id=shape,
        set_name="Set",
        main_stats={"a": 1, "b": 1},
        role_scores=scores,
    )


def _run_equal_choice_allocation(order: list[str]) -> dict[str, str]:
    by_uid = {
        "a": _scored_drive("a", {"High": 10.0, "Later": 10.0}),
        "b": _scored_drive("b", {"High": 10.0, "Later": 10.0}),
    }
    drives = [by_uid[uid] for uid in order]
    strategy = RolePriorityStrategy(
        {"High": {"default_set": "Set"}, "Later": {"default_set": "Set"}},
        {"Set": {"shapes": []}},
        {
            "High": [{"set_pieces": [], "extra_pieces": ["X"]}],
            "Later": [{"set_pieces": [], "extra_pieces": ["X"]}],
        },
    )
    result = strategy.execute(
        {"drives": drives, "all_drives": drives, "tapes": {}},
        ["High", "Later"],
        {"High": "Set", "Later": "Set"},
        priority_groups=[["High"], ["Later"]],
    )
    return {
        role: plan["assigned_extra_drives"][0].uid
        for role, plan in result.items()
    }


class _ReservationSearchStrategy:
    """Deterministic group scorer that exposes the former greedy dead end."""

    def _find_best_group_fit(self, _group, drives, *_args):
        by_uid = {drive.uid: drive for drive in drives}
        available = frozenset(by_uid)
        selected, score = {
            frozenset({"a", "b", "c"}): (("a", "b"), 300.0),
            frozenset({"b", "c"}): (("b", "c"), 200.0),
            frozenset({"a", "c"}): (("a", "c"), 200.0),
            frozenset({"b"}): (("b",), 100.0),
            frozenset({"c"}): (("c",), 10.0),
        }.get(available, ((), -1.0))
        return {
            "Later": {
                "valid": score >= 0,
                "score": score,
                "rank_score": score,
                "assigned_set_drives": [],
                "assigned_extra_drives": [by_uid[uid] for uid in selected],
            },
        }

    def _allocated_drive_uids(self, allocation):
        return {
            drive.uid
            for plan in allocation.values()
            for field in ("assigned_set_drives", "assigned_extra_drives")
            for drive in plan.get(field, [])
        }

    def _recover_equal_priority_group(self, group, *_args, **_kwargs):
        return {role_name: {"valid": False, "score": -1.0} for role_name in group}
