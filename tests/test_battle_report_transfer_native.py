# 验证原生战报跨库往返、外置日志脱敏和同摘要不同证据的原子拒绝。
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.domain.battle_report_transfer import portable_battle_evidence
from src.integrations.battle_report_bundle import (
    decode_battle_report_bundle, encode_battle_report_bundle,
)
from src.services.battle_report_transfer_service import BattleReportTransferService
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from src.storage.sqlite.user_data_support import SCHEMA_VERSION
from tests.test_battle_report_transfer_dao import _insert_summary
from tests.test_battle_report_transfer_policy import _bundle_payload
from tests.native_execution_fixture import execution_evidence


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _persist(dao: UserDataDao, lane: str, journal: Path) -> int:
    operation = "portable-native-" + lane
    snapshots = [
        {"id": "9007199254740999", "observedUs": "1788700000000000", "complete": True, "ended": False,
         "effects": [{"key": "Buff_Test_C", "stacks": 3, "kind": 255, "name": ""}]},
        {"complete": True, "ended": False, "effects": []},
        {"complete": False, "ended": False, "status": "component_enumeration_incomplete",
         "effects": [{"key": "GE_Target_C", "inhibited": False}]},
    ]
    rows = []
    for ordinal, snapshot in enumerate(snapshots):
        hit_id = str(9007199254740993 + ordinal)
        raw = {"hitId": hit_id, "unixUs": "1788800000000000", "damage": 10.0,
               "metadataKnown": False, "criticalKnown": ordinal != 2,
               "critical": ordinal == 0 if ordinal != 2 else None,
               "criticalSource": "native_execution" if ordinal != 2 else None,
               "damageTypeKnown": ordinal != 1, "damageType": 2 if ordinal != 1 else None,
               "gameplayEffectName": "GE_Xiaozhen_Skill1_Damage_C", "attackKey": "origin-key",
               "association": "native_message_target_ordinal", "messageIndex": "9007199254741001",
               "serverTimestampBits": "18446744073709551615", "componentOrdinal": ordinal,
               "settlementKey": f"receiver:8:99:0:{ordinal}", "lastTargetComponent": ordinal == 2,
               "victimHp": 500000.0625, "victimMaxHp": 1000000.0, "maxHpReduction": None,
               "victimHpStage": "server_settlement_payload", "victimMaxHpStage": "before_settlement_dispatch",
               "victimMaxHpObservedUnixUs": "1788800000000010",
               "hitObservedUnixUs": "1788799999999990", "settlementObservedUnixUs": "1788800000000000",
               "attackerEffectsObservedUnixUs": "1788799999999991", "victimEffectsObservedUnixUs": None,
               "attackerEffects": snapshot, "victimEffects": snapshots[2 - ordinal],
               "executionEvidence": execution_evidence() if ordinal == 0 else None,
               "futureField": {"number": 18446744073709551615}}
        row = {"sequence": hit_id, "relative_time_seconds": ordinal / 10,
               "character_id": 1072, "character_name": "测试角色", "character_known": True,
               "direction": "outgoing", "damage": 10.0, "follow_up_damage": 0.0,
               "total_damage": 10.0, "follow_up_labels": []}
        if lane == "native":
            row["native_capture"] = {"providerId": "provider", "captureId": "capture",
                                     "hitId": hit_id, "rawHit": raw}
        rows.append(row)
    dao.begin_battle_axis_capture(capture_operation_id=operation,
                                 captured_at_utc="2026-09-10T01:00:00+00:00", account_generation=1)
    dao.append_battle_axis_page(capture_operation_id=operation, page={
        "contract_version": 5, "battle_record_id": lane, "generation": "1", "complete": True,
        "first_available_cursor": "1", "next_cursor": None, "total_hits": "3", "retained_hits": 3,
        "rows": rows,
    })
    record_id = _insert_summary(dao, operation, 30.0)
    native = {"providerId": "provider", "captureId": "capture", "transportComplete": True,
              "sourceCoverage": "unknown", "rawCapturePath": str(journal),
              "rawCapture": {"path": str(journal), "complete": False, "partial": "1"}}
    summary = {"total_damage": 30.0, "total_hits": 3, "native_capture": native,
               "raw_capture_path": str(journal)}
    summary_text = _dump(summary)
    dao._db().execute("UPDATE battle_record SET raw_summary_json=?, raw_summary_sha256=? WHERE battle_record_id=?",
                      (summary_text, hashlib.sha256(summary_text.encode()).hexdigest(), record_id))
    dao._db().commit()
    dao.finalize_battle_axis_capture(
        capture_operation_id=operation, battle_record_id=record_id,
        record={"contract_version": 5, "battle_record_id": lane, "generation": "1", "axis_complete": True,
                "axis_first_sequence": rows[0]["sequence"], "axis_total_hits": "3", "time_stop_intervals": [],
                "native_capture": native, "calc_capture": {"comparison_id": "original-pair", "source": lane}},
        observed_characters={}, source_inventory_snapshot_id=None, static_dataset_id="fixture",
        static_schema_version=32, character_profiles={}, character_stat_snapshots={},
        finalized_at_utc="2026-09-10T01:00:10+00:00",
    )
    return record_id


def _export(dao: UserDataDao, record_id: int) -> dict:
    service = BattleReportTransferService.__new__(BattleReportTransferService)
    with patch.object(service, "_analysis_projection", return_value={"target_inference": None, "stale": True}):
        return service._export_report(
            record=dao.load_battle_record(record_id), account_name="测试账号",
            row_graph=dao.load_battle_report_transfer_rows(record_id), target_condition=None,
            frozen_build=None, build_edit=None, import_origin=None, import_locks={}, unavailable=[],
        )


class NativeBattleReportTransferTests(unittest.TestCase):
    def test_native_and_packet_pair_round_trip_without_external_files_or_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = root / "private-runtime-evidence.jsonl"
            journal.write_text("PRIVATE_JOURNAL_CONTENT_SHOULD_NOT_TRAVEL", encoding="utf-8")
            with UserDataDao(root / "source.sqlite3", account_id="source") as source:
                ids = [_persist(source, lane, journal) for lane in ("native", "packet")]
                original = [source.load_battle_report_transfer_rows(i) for i in ids]
                exported = [_export(source, i) for i in ids]
                self.assertEqual(original, [source.load_battle_report_transfer_rows(i) for i in ids])
            payload = _bundle_payload(SCHEMA_VERSION)
            payload["reports"] = exported
            payload["manifest"]["report_count"] = 2
            restored = decode_battle_report_bundle(encode_battle_report_bundle(payload))
            reports = BattleReportTransferService._validate_bundle(restored)
            text = _dump(restored)
            self.assertNotIn("private-runtime-evidence", text)
            self.assertNotIn("PRIVATE_JOURNAL_CONTENT_SHOULD_NOT_TRAVEL", text)
            for report in reports:
                tables = report["database_rows"]["tables"]
                for table, raw_key, sha_key in (
                    ("battle_record", "raw_summary_json", "raw_summary_sha256"),
                    ("battle_axis_capture", "raw_record_json", "raw_record_sha256"),
                ):
                    row = tables[table][0]
                    self.assertEqual(hashlib.sha256(row[raw_key].encode()).hexdigest(), row[sha_key])
                self.assertEqual(tables["battle_record"][0]["raw_summary_sha256"], report["nte_core"]["summary"]["sha256"])
                self.assertEqual(tables["battle_axis_capture"][0]["raw_record_sha256"], report["nte_core"]["record"]["sha256"])
            graphs = [BattleReportTransferService._prepare_import_row_graph(
                report, bundle=restored["bundle"], imported_at_utc="2026-09-10T02:00:00+00:00") for report in reports]
            with UserDataDao(root / "target.sqlite3", account_id="target") as target:
                outcome = target.import_battle_report_transfer_rows(graphs)
                imported = [target.load_battle_report_transfer_rows(i) for i in outcome["imported_battle_record_ids"]]
                self.assertEqual(2, len(imported))
                for index, graph in enumerate(imported):
                    self.assertNotIn("derived_analysis", graph["tables"])
                    source_hits = original[index]["tables"]["battle_hit_evidence"]
                    self.assertEqual([row["raw_hit_json"] for row in source_hits],
                                     [row["raw_hit_json"] for row in graph["tables"]["battle_hit_evidence"]])
                    record = json.loads(graph["tables"]["battle_axis_capture"][0]["raw_record_json"])
                    self.assertEqual({"comparison_id": "original-pair", "source": ("native", "packet")[index]}, record["calc_capture"])
                    self.assertIsNone(record["native_capture"]["rawCapture"]["path"])
                self.assertEqual(2, target.import_battle_report_transfer_rows(graphs)["skipped_existing_count"])

    def test_same_summary_changed_raw_hit_conflicts_and_rolls_back_whole_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with UserDataDao(root / "source.sqlite3", account_id="source") as source:
                record_id = _persist(source, "native", root / "journal.jsonl")
                graph = source.load_battle_report_transfer_rows(record_id)
                other_id = _insert_summary(source, "additional-operation", 1.0)
                other = source.load_battle_report_transfer_rows(other_id)
            with UserDataDao(root / "target.sqlite3", account_id="target") as target:
                imported = target.import_battle_report_transfer_rows([graph])["imported_battle_record_ids"][0]
                original = target.load_battle_report_transfer_rows(imported)
                changed = deepcopy(graph)
                hit = json.loads(changed["tables"]["battle_hit_evidence"][0]["raw_hit_json"])
                hit["native_capture"]["rawHit"]["critical"] = False
                changed["tables"]["battle_hit_evidence"][0]["raw_hit_json"] = _dump(hit)
                with self.assertRaisesRegex(UserDataValidationError, "原始逐击证据"):
                    target.import_battle_report_transfer_rows([other, changed])
                self.assertEqual(1, len(target.list_battle_records()))
                self.assertEqual(original, target.load_battle_report_transfer_rows(imported))
                # A pre-fix package with local paths still deduplicates after portable normalization.
                self.assertEqual(1, target.import_battle_report_transfer_rows([graph])["skipped_existing_count"])

    def test_nested_payload_copies_are_cleaned_without_mutating_or_erasing_evidence(self) -> None:
        raw = {"native_capture": {"rawCapturePath": "C:/private/raw.jsonl",
                                  "rawCapture": {"path": "C:/private/raw.jsonl", "partial": "2"}},
               "future": 18446744073709551615, "rawHit": {"attackerEffects": None}}
        value = {"raw_hit_json": _dump(raw), "native_evidence": {"payload_json": _dump(raw)},
                 "raw": raw, "ordinary_path": "/Game/GE_Known_C"}
        before = deepcopy(value)
        clean = portable_battle_evidence(value)
        self.assertEqual(before, value)
        self.assertNotIn("C:/private", _dump(clean))
        self.assertEqual(18446744073709551615, json.loads(clean["raw_hit_json"])["future"])
        self.assertEqual("/Game/GE_Known_C", clean["ordinary_path"])

    def test_import_checks_old_hash_before_path_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with UserDataDao(root / "source.sqlite3", account_id="source") as source:
                record_id = _persist(source, "native", root / "journal.jsonl")
                graph = source.load_battle_report_transfer_rows(record_id)
            graph["tables"]["battle_axis_capture"][0]["raw_record_sha256"] = "0" * 64
            with UserDataDao(root / "target.sqlite3", account_id="target") as target:
                with self.assertRaisesRegex(UserDataValidationError, "record SHA-256"):
                    target.import_battle_report_transfer_rows([graph])
                self.assertEqual([], target.list_battle_records())


if __name__ == "__main__":
    unittest.main()
