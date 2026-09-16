# 验证原生 Buff 事件与命中快照经过正式战报保存后保持完整。
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_battle_axis_dao import BattleAxisDaoTests


class NativeBuffPersistenceTests(unittest.TestCase):
    def test_callbacks_and_hit_snapshot_survive_finalization(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.dao = UserDataDao(Path(temporary) / "user.sqlite3", account_id="fixture")
            try:
                operation = "buff-fixture"
                self.dao.begin_battle_axis_capture(
                    capture_operation_id=operation,
                    captured_at_utc="2026-09-13T00:00:00+00:00", account_generation=1,
                )
                native = {"captureId": "capture:1", "transportComplete": True,
                          "contextEvents": [
                              {"recordType": "event", "schemaVersion": 1, "contextId": "1",
                               "observedUnixUs": "101", "kind": "effect_magnitude_query",
                               "evidenceOrigin": "supplemental_readonly_query", "baseMagnitude": 200,
                               "applicationStatus": "unverified", "effectHandle": "9"},
                              {"recordType": "event", "schemaVersion": 1, "contextId": "2",
                               "observedUnixUs": "102", "kind": "future_evidence_kind",
                               "payload": {"unknown": None, "values": [0, 2]}}],
                          "buffEvents": [{"eventId": 1, "kind": "initial_presence", "evidence": {"key": "12:34"}},
                                         {"eventId": 2, "kind": "notification", "evidence": {"instanceKey": "56:78:9", "stacks": 3}}]}
                hit_native = {"captureId": "capture:1", "rawHit": {
                    "hitId": "1", "attackerEffects": {"id": "7", "observedUs": "105",
                    "key": "12:34", "effects": [{"instanceKey": "56:78:9", "stacks": 3}]}}}
                self.dao.append_battle_axis_page(capture_operation_id=operation, page={
                    "contract_version": 5, "battle_record_id": "capture:1", "generation": "1",
                    "complete": True, "first_available_cursor": "1", "next_cursor": None,
                    "total_hits": "1", "retained_hits": 1, "rows": [{
                        "sequence": "1", "direction": "outgoing", "damage": 100.0,
                        "overkill_damage": 0.0, "follow_up_labels": [], "native_capture": hit_native,
                    }],
                })
                record_id = BattleAxisDaoTests._insert_summary(self, operation)
                self.dao.finalize_battle_axis_capture(
                    capture_operation_id=operation, battle_record_id=record_id,
                    record={"contract_version": 5, "battle_record_id": "capture:1", "generation": "1",
                            "axis_complete": True, "axis_first_sequence": "1", "axis_total_hits": "1",
                            "native_capture": native},
                    observed_characters={}, source_inventory_snapshot_id=None,
                    static_dataset_id="fixture", static_schema_version=32,
                    character_profiles={}, character_stat_snapshots={},
                    finalized_at_utc="2026-09-13T00:00:01+00:00",
                )
                # 此处核对保存内容；生产分析由 Rust 直接读取这些正式列。
                connection = self.dao._db()
                saved_record = connection.execute(
                    "SELECT raw_record_json FROM battle_axis_capture WHERE battle_record_id=?", (record_id,),
                ).fetchone()[0]
                saved_hit = connection.execute("SELECT raw_hit_json FROM battle_hit_evidence").fetchone()[0]
                self.assertEqual(native, json.loads(saved_record)["native_capture"])
                self.assertEqual(hit_native, json.loads(saved_hit)["native_capture"])
            finally:
                self.dao.close()
