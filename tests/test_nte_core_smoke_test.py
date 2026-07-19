# 测试本地核心组件抓取背包快照时的稳定性判定。
from __future__ import annotations

import queue
import unittest

from tools.nte_core_smoke_test import wait_for_inventory_snapshots


def inventory_snapshot(count: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "event.inventory.snapshot",
        "params": {
            "complete": True,
            "item_count": count,
            "items": [
                {"uid": {"slot": 0, "serial": index + 1}}
                for index in range(count)
            ],
        },
    }


class FakeNteCoreClient:
    def __init__(self, *events: dict) -> None:
        self.events: queue.Queue[dict] = queue.Queue()
        self.diagnostics: queue.Queue[str] = queue.Queue()
        for event in events:
            self.events.put(event)


class NteCoreSmokeTestTests(unittest.TestCase):
    def test_uses_latest_snapshot_after_login_stream_settles(self):
        client = FakeNteCoreClient(inventory_snapshot(2), inventory_snapshot(5))

        event, item_count, settled = wait_for_inventory_snapshots(
            client,
            timeout=1.0,
            settle_seconds=0.01,
        )

        self.assertTrue(settled)
        self.assertEqual(5, item_count)
        self.assertEqual(5, event["params"]["item_count"])

    def test_accepts_a_small_account_without_a_minimum_threshold(self):
        client = FakeNteCoreClient(inventory_snapshot(1))

        event, item_count, settled = wait_for_inventory_snapshots(
            client,
            timeout=1.0,
            settle_seconds=0.01,
        )

        self.assertTrue(settled)
        self.assertEqual(1, item_count)
        self.assertEqual(1, event["params"]["item_count"])

    def test_does_not_keep_the_historical_maximum(self):
        client = FakeNteCoreClient(inventory_snapshot(5), inventory_snapshot(4))

        event, item_count, settled = wait_for_inventory_snapshots(
            client,
            timeout=1.0,
            settle_seconds=0.01,
        )

        self.assertTrue(settled)
        self.assertEqual(4, item_count)
        self.assertEqual(4, event["params"]["item_count"])


if __name__ == "__main__":
    unittest.main()
