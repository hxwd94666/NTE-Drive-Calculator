# 测试连续背包快照的稳定判定和任意账号数量兼容性。
from __future__ import annotations

import unittest

from src.services.inventory_snapshot_stabilizer import InventorySnapshotStabilizer


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def item(serial: int, *, level: int = 1, equipped: bool = False) -> dict:
    return {
        "uid": {"slot": 7, "serial": serial},
        "kind": "module",
        "item_id": f"item-{serial}",
        "level": level,
        "max_level": 20,
        "locked": False,
        "equipped": equipped,
        "main_stats": [],
        "sub_stats": [],
    }


def snapshot(
    *items: dict,
    generation: int = 1,
    sequence: int = 1,
    complete: bool = True,
) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "event.inventory.snapshot",
        "params": {
            "complete": complete,
            "item_count": len(items),
            "items": list(items),
            "generation": generation,
            "sequence": sequence,
        },
    }


class InventorySnapshotStabilizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.stabilizer = InventorySnapshotStabilizer(3.0, clock=self.clock)

    def _commit_ready(self) -> int:
        stable = self.stabilizer.ready()
        self.assertIsNotNone(stable)
        assert stable is not None
        self.stabilizer.mark_committed(stable.fingerprint)
        return stable.item_count

    def test_accepts_any_complete_inventory_size_without_minimum(self) -> None:
        offered = self.stabilizer.offer(snapshot(item(1), sequence=1))
        self.assertEqual("collecting", offered.status)
        self.clock.advance(3.0)
        self.assertEqual(1, self._commit_ready())

        self.clock.advance(1.0)
        empty = self.stabilizer.offer(snapshot(generation=2, sequence=1))
        self.assertEqual("collecting", empty.status)
        self.clock.advance(3.0)
        self.assertEqual(0, self._commit_ready())

    def test_login_stream_commits_latest_snapshot_after_content_quiets(self) -> None:
        first = self.stabilizer.offer(snapshot(item(1), sequence=1))
        self.assertEqual(1, first.added_count)
        self.clock.advance(2.0)
        second = self.stabilizer.offer(snapshot(item(1), item(2), sequence=2))
        self.assertEqual("changed", second.status)
        self.assertEqual(1, second.added_count)

        self.clock.advance(2.9)
        self.assertIsNone(self.stabilizer.ready())
        self.clock.advance(0.1)
        self.assertEqual(2, self._commit_ready())

    def test_identical_events_do_not_extend_the_quiet_window(self) -> None:
        self.stabilizer.offer(snapshot(item(1), sequence=1))
        self.clock.advance(2.0)
        duplicate = self.stabilizer.offer(snapshot(item(1), sequence=2))
        self.assertEqual("duplicate", duplicate.status)
        self.clock.advance(1.0)
        self.assertEqual(1, self._commit_ready())

    def test_same_count_but_changed_content_restarts_stability(self) -> None:
        self.stabilizer.offer(snapshot(item(1, level=1), sequence=1))
        self.clock.advance(2.5)
        changed = self.stabilizer.offer(snapshot(item(1, level=2), sequence=2))
        self.assertEqual("changed", changed.status)
        self.assertEqual(0, changed.added_count)
        self.clock.advance(2.5)
        self.assertIsNone(self.stabilizer.ready())
        self.clock.advance(0.5)
        stable = self.stabilizer.ready()
        self.assertEqual(2, stable.payload["items"][0]["level"])

    def test_later_smaller_inventory_is_a_valid_new_stable_version(self) -> None:
        self.stabilizer.offer(snapshot(item(1), item(2), sequence=1))
        self.clock.advance(3.0)
        self.assertEqual(2, self._commit_ready())

        self.clock.advance(1.0)
        removed = self.stabilizer.offer(snapshot(item(1), sequence=2))
        self.assertEqual(1, removed.removed_count)
        self.clock.advance(3.0)
        self.assertEqual(1, self._commit_ready())

    def test_returning_to_committed_content_cancels_pending_cycle(self) -> None:
        original = snapshot(item(1), sequence=1)
        self.stabilizer.offer(original)
        self.clock.advance(3.0)
        self._commit_ready()

        self.stabilizer.offer(snapshot(item(1), item(2), sequence=2))
        reverted = self.stabilizer.offer(snapshot(item(1), sequence=3))
        self.assertEqual("reverted", reverted.status)
        self.assertFalse(self.stabilizer.has_pending_changes)

    def test_rejects_out_of_order_incomplete_and_mismatched_snapshots(self) -> None:
        self.stabilizer.offer(snapshot(item(1), generation=2, sequence=5))
        older = self.stabilizer.offer(snapshot(item(1), item(2), generation=2, sequence=4))
        self.assertEqual("ignored", older.status)

        incomplete = self.stabilizer.offer(snapshot(item(1), generation=3, complete=False))
        self.assertEqual("ignored", incomplete.status)

        mismatched = snapshot(item(1), generation=3, sequence=2)
        mismatched["params"]["item_count"] = 99
        invalid = self.stabilizer.offer(mismatched)
        self.assertEqual("ignored", invalid.status)

    def test_item_and_stat_order_do_not_create_false_changes(self) -> None:
        first = item(1)
        first["sub_stats"] = [
            {"property_id": "b", "value": 2, "percent": False},
            {"property_id": "a", "value": 1, "percent": False},
        ]
        second = item(1)
        second["sub_stats"] = list(reversed(first["sub_stats"]))
        self.stabilizer.offer(snapshot(first, item(2), sequence=1))
        duplicate = self.stabilizer.offer(snapshot(item(2), second, sequence=2))
        self.assertEqual("duplicate", duplicate.status)


if __name__ == "__main__":
    unittest.main()
