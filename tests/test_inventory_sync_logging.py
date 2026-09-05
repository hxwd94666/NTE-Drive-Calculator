# 验证背包同步诊断的限频、退出汇总和敏感载荷隔离。
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.observability import OperationContext
from src.services.inventory_snapshot_stabilizer import InventorySnapshotStabilizer, SnapshotOfferResult
from src.services.inventory_sync_logging import (
    InventorySyncDiagnostics,
    inventory_core_log_fields,
    inventory_payload_log_fields,
)


class InventorySyncLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 0.0
        self.events: list[tuple[str, str, dict]] = []
        clock = patch("src.services.inventory_sync_logging.time.monotonic", side_effect=lambda: self.now)
        writer = patch(
            "src.services.inventory_sync_logging.log_event",
            side_effect=lambda level, event, message, context, **fields: self.events.append(
                (level, event, fields)
            ),
        )
        clock.start()
        writer.start()
        self.addCleanup(clock.stop)
        self.addCleanup(writer.stop)
        self.diagnostics = InventorySyncDiagnostics(OperationContext.create("inventory_sync"))

    def test_repeated_outcomes_are_throttled_but_final_counts_are_exact(self) -> None:
        event = {"complete": True, "item_count": 0, "items": [], "characters": []}
        for _ in range(100):
            self.diagnostics.record(event, SnapshotOfferResult("unchanged"), guard_item_count=None)
        self.assertEqual(1, len(self.events))
        self.now = 30.0
        self.diagnostics.record(event, SnapshotOfferResult("unchanged"), guard_item_count=None)
        self.assertEqual(2, len(self.events))
        self.diagnostics.summary(phase="stopped", pending_item_count=None, snapshot_id=7, final=True)
        summary = self.events[-1][2]
        self.assertEqual(101, summary["processed_event_count"])
        self.assertEqual(101, summary["outcome_counts"]["unchanged"])
        self.assertEqual(0, summary["committed_count"])
        self.assertTrue(summary["final"])
        self.assertTrue(all(level == "INFO" for level, _, _ in self.events))

    def test_idle_and_failed_sessions_produce_summary_without_claiming_received_data(self) -> None:
        self.now = 29.0
        self.diagnostics.summary(phase="listening", pending_item_count=None, snapshot_id=3)
        self.assertFalse(self.events)
        self.now = 30.0
        self.diagnostics.summary(phase="listening", pending_item_count=None, snapshot_id=3)
        self.assertEqual(0, self.events[-1][2]["processed_event_count"])
        self.assertIsNone(self.events[-1][2]["seconds_since_last_processed_event"])
        self.now = 31.0
        self.diagnostics.summary(phase="failed", pending_item_count=None, snapshot_id=3, final=True)
        self.assertEqual(2, len(self.events))
        self.assertEqual("failed", self.events[-1][2]["phase"])

    def test_rejection_logs_fixed_reason_without_uid_or_raw_exception(self) -> None:
        uid = {"slot": 987654321, "serial": 876543219}
        event = {"complete": True, "item_count": 2, "items": [{"uid": uid}, {"uid": uid}]}
        result = InventorySnapshotStabilizer().offer(event)
        self.assertEqual("duplicate_item_uid", result.reason_code)
        self.diagnostics.record(event, result, guard_item_count=None)
        output = json.dumps(self.events, ensure_ascii=False)
        self.assertNotIn("987654321", output)
        self.assertNotIn("876543219", output)
        self.assertNotIn(result.reason, output)
        self.assertEqual("ignored", self.events[-1][2]["outcome"])
        self.assertEqual("duplicate_item_uid", self.events[-1][2]["reason_code"])

    def test_invalid_scalar_fields_cannot_smuggle_payload_into_logs(self) -> None:
        secret = "private_payload_marker"
        event = {
            "complete": {"raw": secret}, "item_count": secret, "character_count": True,
            "items": [{"uid": secret, "names": secret}], "characters": secret,
            "generation": {"raw": secret}, "sequence": secret,
        }
        fields = inventory_payload_log_fields(event)
        self.assertNotIn(secret, json.dumps(fields))
        self.assertEqual("invalid", fields["characters_field"])
        self.assertIsNone(fields["generation"])
        self.assertIsNone(fields["declared_character_count"])
        self.assertEqual("missing", inventory_payload_log_fields({})["characters_field"])
        self.assertEqual("list", inventory_payload_log_fields({"characters": []})["characters_field"])
        self.assertEqual({}, inventory_core_log_fields({"core_version": secret}))
        self.assertEqual(
            {"core_version": "0.4.4", "data_version": "1"},
            inventory_core_log_fields({"core_version": "0.4.4", "data_version": "1"}),
        )


if __name__ == "__main__":
    unittest.main()
