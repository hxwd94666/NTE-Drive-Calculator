# 验证重复归属扫描按目标缩小候选后仍保留时间、伤害及生命证据边界。
NTE_TEST_TIER = "core"
from concurrent.futures import CancelledError
from dataclasses import replace
import unittest

from src.services.battle_report.replay.audit import BattleHitReplayAuditService
from tests.battle_report.replay.test_audit_repairs import _hit


def _candidate(event, at_us, effect="GE_First", **changes):
    return replace(
        _hit(event, at_us, effect),
        target_hp_before=1000.0, target_hp_after=900.0, **changes,
    )


class BattleReplayConflictWindowTests(unittest.TestCase):
    def conflicts(self, *hits):
        return BattleHitReplayAuditService.damage_attribution_conflict_ids(hits)

    def test_700ms_boundary_includes_endpoint_and_excludes_one_microsecond_later(self):
        first = _candidate("first", 0)
        boundary = _candidate("boundary", 700_000, "GE_Second")
        outside = replace(boundary, event_id="outside", relative_time_us=700_001)
        self.assertEqual(frozenset({"first", "boundary"}), self.conflicts(boundary, first))
        self.assertEqual(frozenset(), self.conflicts(first, outside))

    def test_target_and_half_casefold_match_but_effect_casefold_excludes(self):
        first = _candidate("first", 0, target_id="BOSS", scope_half="UPPER")
        second = _candidate("second", 0, "ge_second")
        self.assertEqual(frozenset({"first", "second"}), self.conflicts(first, second))
        for changed in (
            replace(second, gameplay_effect_id="ge_FIRST"),
            replace(second, target_id="other"),
            replace(second, scope_half="lower"),
            replace(second, target_hp_before=None),
            replace(second, target_hp_after=None),
            replace(second, direction="incoming"),
        ):
            with self.subTest(changed=changed.event_id, target=changed.target_id):
                self.assertEqual(frozenset(), self.conflicts(first, changed))

    def test_interleaved_targets_and_missing_hp_do_not_shift_bucket_position(self):
        first = _candidate("first", 0)
        no_hp = replace(first, event_id="no-hp", relative_time_us=1, target_hp_before=None)
        other = _candidate("other", 2, "GE_Second", target_id="elsewhere")
        second = _candidate("second", 3, "GE_Second")
        third = _candidate("third", 4, "GE_Third")
        self.assertEqual(
            frozenset({"first", "second", "third"}),
            self.conflicts(third, no_hp, first, other, second),
        )

    def test_damage_tolerance_uses_first_sorted_hit(self):
        first = _candidate("first", 0)
        equal = replace(_candidate("equal", 1, "GE_Second"), damage=100.5)
        unequal = replace(equal, event_id="unequal", damage=100.500001)
        self.assertEqual(frozenset({"first", "equal"}), self.conflicts(equal, first))
        self.assertEqual(frozenset(), self.conflicts(first, unequal))
        large = replace(first, damage=1_000_000.0)
        large_equal = replace(equal, damage=1_000_001.0)
        self.assertEqual(frozenset({"first", "equal"}), self.conflicts(large, large_equal))

    def test_hp_overlap_is_strict_but_endpoint_tolerance_is_inclusive(self):
        first = _candidate("first", 0)
        touching = replace(_candidate("second", 1, "GE_Second"),
            target_hp_before=900.0, target_hp_after=800.0)
        endpoint = replace(touching, target_hp_before=899.5, target_hp_after=899.5)
        outside = replace(endpoint, target_hp_before=899.499999, target_hp_after=899.499999)
        self.assertEqual(frozenset(), self.conflicts(first, touching))
        self.assertEqual(frozenset({"first", "second"}), self.conflicts(first, endpoint))
        self.assertEqual(frozenset(), self.conflicts(first, outside))
        reversed_hp = replace(touching, target_hp_before=950.0, target_hp_after=1050.0)
        self.assertEqual(frozenset({"first", "second"}), self.conflicts(first, reversed_hp))

    def test_progress_retains_full_outgoing_axis_and_cancellation(self):
        hits = tuple(replace(_candidate(str(i), i), target_hp_before=None) for i in range(130))
        progress = []
        BattleHitReplayAuditService.damage_attribution_conflict_ids(hits, progress_callback=progress.append)
        self.assertEqual([0, 64, 128, 130], [row.completed for row in progress])
        self.assertEqual({130}, {row.total for row in progress})

        def cancel(row):
            if row.completed == 64:
                raise CancelledError

        with self.assertRaises(CancelledError):
            BattleHitReplayAuditService.damage_attribution_conflict_ids(hits, progress_callback=cancel)


