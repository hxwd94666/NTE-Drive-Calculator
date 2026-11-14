# 验证失败恢复中当前配装与前序预留槽位的联合匹配。
from __future__ import annotations

import unittest

import numpy as np

from src.models.equipment import Drive
from src.optimizer.reservations.deferred_drive_reservations import (
    DeferredDriveReservationState,
    DeferredDriveSlot,
)
from src.optimizer.reservations.reservation_matching import match_reserved_group_slots


NTE_TEST_TIER = "core"


class ReservationMatchingTests(unittest.TestCase):
    def test_independent_conflicts_keep_one_return_drive_in_each_pool(self):
        profits = np.array([[100, 90, -1, -1], [-1, -1, 100, 90]], dtype=float)

        result = match_reserved_group_slots(
            profits, profits, ("a", "b", "c", "d"), (("a", "b"), ("c", "d")),
        )

        self.assertIsNotNone(result)
        rows, columns = result
        self.assertEqual([0, 1], rows.tolist())
        self.assertEqual([0, 2], columns.tolist())

    def test_overlapping_pools_obey_hall_constraint_and_preserve_best_current_score(self):
        profits = np.array([[50, 100, 200]], dtype=float)

        result = match_reserved_group_slots(
            profits, profits, ("a", "b", "c"), (("a", "b"), ("b", "c")),
        )

        self.assertIsNotNone(result)
        self.assertEqual([2], result[1].tolist())

    def test_overlapping_tight_pool_cannot_be_consumed_even_with_two_candidates_per_slot(self):
        profits = np.array([[100, 90, 1]], dtype=float)

        result = match_reserved_group_slots(
            profits, profits, ("a", "b", "spare"), (("a", "b"), ("a", "b")),
        )

        self.assertIsNotNone(result)
        self.assertEqual([2], result[1].tolist())

    def test_external_reservation_column_is_not_a_current_candidate(self):
        profits = np.array([[10]], dtype=float)

        result = match_reserved_group_slots(profits, profits, ("a",), (("a", "outside"),))

        self.assertIsNotNone(result)
        self.assertEqual([0], result[1].tolist())
        impossible = np.array([[-1]], dtype=float)
        self.assertIsNone(match_reserved_group_slots(
            impossible, impossible, ("a",), (("a", "outside"),),
        ))

    def test_impossible_previous_matching_returns_none_despite_enough_total_columns(self):
        profits = np.array([[1, 2, 3]], dtype=float)

        self.assertIsNone(match_reserved_group_slots(
            profits, profits, ("a", "b", "c"), (("a",), ("a",)),
        ))
        self.assertIsNone(match_reserved_group_slots(profits, profits, ("a", "b", "c"), ((),)))

    def test_more_required_rows_than_unique_drives_returns_none(self):
        profits = np.array([[1, 2], [3, 4]], dtype=float)

        self.assertIsNone(match_reserved_group_slots(
            profits, profits, ("a", "b"), (("a", "b"),),
        ))

    def test_ranking_objective_and_profit_legality_are_separate(self):
        ranking = np.array([[50, 100, 9999]], dtype=float)
        profit = np.array([[500, 1, -10000]], dtype=float)
        ranking_before = ranking.copy()
        profit_before = profit.copy()

        result = match_reserved_group_slots(
            ranking, profit, ("a", "b", "illegal"), (("a", "outside"),),
        )

        self.assertIsNotNone(result)
        self.assertEqual([1], result[1].tolist())
        np.testing.assert_array_equal(ranking_before, ranking)
        np.testing.assert_array_equal(profit_before, profit)

    def test_projection_excludes_consumed_uids_and_keeps_slot_order_frozen(self):
        drives = {uid: _drive(uid) for uid in ("a", "b", "c", "d")}
        state = DeferredDriveReservationState()
        state.register([
            (_slot("z", "c", ("c", "d")), drives.values()),
            (_slot("a", "a", ("a", "b")), drives.values()),
        ])
        before = state.remaining_candidate_uids
        state.commit({"a"})

        self.assertEqual((("a", "b"), ("c", "d")), before)
        self.assertEqual((("b",), ("c", "d")), state.remaining_candidate_uids)
        profits = np.array([[100, 10]], dtype=float)
        result = match_reserved_group_slots(
            profits, profits, ("b", "d"), state.remaining_candidate_uids,
        )
        self.assertIsNotNone(result)
        self.assertEqual([1], result[1].tolist())
        self.assertEqual(frozenset({"a"}), state.consumed_uids)
        self.assertEqual(2, len(state.finalize()))


def _drive(uid):
    return Drive(uid=uid, quality="Gold", area=2, shape_id="H_2", main_stats={"攻击力": 1, "生命值": 1})


def _slot(key, baseline, candidates):
    return DeferredDriveSlot(
        key=key, group_index=0, role_name="Previous", slot_type="extra",
        slot_index=0, baseline_uid=baseline, candidate_uids=candidates,
    )
