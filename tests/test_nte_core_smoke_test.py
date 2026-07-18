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
            # 协议以 item_count 为准。这里保留最小 items 数组，也能避免测试耗时
            # 受到终端输出量影响。
            "items": [{"uid": {"slot": 0, "serial": 1}}] if count else [],
        },
    }


class FakeNteCoreClient:
    def __init__(self, *events: dict) -> None:
        self.events: queue.Queue[dict] = queue.Queue()
        self.diagnostics: queue.Queue[str] = queue.Queue()
        for event in events:
            self.events.put(event)


class NteCoreSmokeTestTests(unittest.TestCase):
    def test_uses_largest_snapshot_after_login_stream_settles(self):
        client = FakeNteCoreClient(inventory_snapshot(12), inventory_snapshot(730))

        event, item_count, settled = wait_for_inventory_snapshots(
            client,
            timeout=1.0,
            expected_min_items=700,
            settle_seconds=0.01,
        )

        self.assertTrue(settled)
        self.assertEqual(730, item_count)
        self.assertEqual(730, event["params"]["item_count"])

    def test_does_not_accept_a_small_snapshot_when_minimum_is_not_met(self):
        client = FakeNteCoreClient(inventory_snapshot(12))

        _event, item_count, settled = wait_for_inventory_snapshots(
            client,
            timeout=0.15,
            expected_min_items=700,
            settle_seconds=0.01,
        )

        self.assertFalse(settled)
        self.assertEqual(12, item_count)


if __name__ == "__main__":
    unittest.main()
