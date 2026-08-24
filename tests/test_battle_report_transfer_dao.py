# 验证战报行图导出与事务式导入行为。

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError


def _insert_summary(dao: UserDataDao, operation_id: str, damage: float) -> int:
    payload = {"total_damage": damage, "total_hits": 1, "name": "导出测试"}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    result = dao.insert_auto_summary_snapshot(
        capture_operation_id=operation_id,
        combat_context_kind="non_abyss",
        abyss_floor=None,
        has_first_half=False,
        has_second_half=False,
        captured_at_utc="2026-08-24T01:00:00+00:00",
        finalized_at_utc="2026-08-24T01:00:10+00:00",
        dps_time_mode="subtract_time_stop",
        duration_seconds=10.0,
        total_damage=damage,
        total_dps=damage / 10.0,
        total_damage_taken=0.0,
        total_hits=1,
        character_count=1,
        skill_count=1,
        character_ids=[1072],
        abyss_detected=False,
        abyss_success=False,
        payload_schema_version=1,
        raw_summary_json=raw,
        raw_summary_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
    return int(result["record"]["battle_record_id"])


def _target_condition() -> dict:
    return {
        "target_name": "测试目标",
        "enemy_level": 90,
        "scene": "open_world",
        "enemy_defense_base": 1050.0,
        "enemy_defense_up": 0.0,
        "enemy_defense_add": 0.0,
        "enemy_topple_limit": 50.0,
        "defense_reduction": 0.0,
        "vulnerability": 0.0,
        "resistances": {
            "normal": 0.2,
            "chaos": 0.2,
            "cosmos": 0.2,
            "incantation": 0.2,
            "lakshana": 0.2,
            "nature": 0.2,
            "psyche": 0.2,
            "psychically": 0.2,
        },
        "environment_kind": "open_world",
        "environment_ref": "fixture",
        "selected_target_ids": ["fixture-target"],
        "primary_target_id": "fixture-target",
        "witch_buff_id": "fixture-buff",
        "witch_buff_name_zh": "测试赐福",
        "witch_buff_property_id": "DamageUpGeneralBase",
        "witch_buff_value": 0.15,
        "witch_buff_is_percent": True,
    }


class BattleReportTransferDaoTests(unittest.TestCase):
    def test_import_remaps_axis_and_preserves_raw_hit_order_and_time_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            operation_id = "axis-transfer-operation"
            with UserDataDao(root / "source.sqlite3", account_id="source") as source:
                source.begin_battle_axis_capture(
                    capture_operation_id=operation_id,
                    captured_at_utc="2026-08-24T01:00:00+00:00",
                    account_generation=3,
                )
                source.append_battle_axis_page(
                    capture_operation_id=operation_id,
                    page={
                        "contract_version": 3,
                        "battle_record_id": "nte-record-1",
                        "generation": "3",
                        "complete": True,
                        "first_available_cursor": "10",
                        "next_cursor": "12",
                        "total_hits": "2",
                        "retained_hits": 2,
                        "rows": [
                            {
                                "sequence": "10",
                                "relative_time_seconds": 0.25,
                                "character_id": 1072,
                                "character_name": "测试角色",
                                "character_known": True,
                                "direction": "outgoing",
                                "damage": 10.0,
                                "follow_up_damage": 0.0,
                                "total_damage": 10.0,
                                "follow_up_labels": [],
                            },
                            {
                                "sequence": "11",
                                "relative_time_seconds": 0.5,
                                "character_id": 1072,
                                "character_name": "测试角色",
                                "character_known": True,
                                "direction": "outgoing",
                                "damage": 20.0,
                                "follow_up_damage": 0.0,
                                "total_damage": 20.0,
                                "follow_up_labels": [],
                            },
                        ],
                    },
                )
                source_id = _insert_summary(source, operation_id, 30.0)
                source.finalize_battle_axis_capture(
                    capture_operation_id=operation_id,
                    battle_record_id=source_id,
                    record={
                        "contract_version": 3,
                        "battle_record_id": "nte-record-1",
                        "generation": "3",
                        "axis_complete": True,
                        "axis_first_sequence": "10",
                        "axis_total_hits": "2",
                        "time_stop_intervals": [{
                            "start_offset_seconds": 0.3,
                            "end_offset_seconds": 0.4,
                        }],
                    },
                    observed_characters={},
                    source_inventory_snapshot_id=None,
                    static_dataset_id="fixture",
                    static_schema_version=27,
                    character_profiles={},
                    character_stat_snapshots={},
                    finalized_at_utc="2026-08-24T01:00:10+00:00",
                )
                graph = source.load_battle_report_transfer_rows(source_id)
            assert graph is not None

            with UserDataDao(root / "target.sqlite3", account_id="target") as target:
                outcome = target.import_battle_report_transfer_rows([graph])
                imported_id = int(outcome["imported_battle_record_ids"][0])
                evidence = target.load_battle_axis_evidence(imported_id)
                imported_graph = target.load_battle_report_transfer_rows(imported_id)

            assert evidence is not None and imported_graph is not None
            self.assertEqual([10.0, 20.0], [row["damage"] for row in evidence["hits"]])
            self.assertEqual(1, len(evidence["time_stop_intervals"]))
            capture = imported_graph["tables"]["battle_axis_capture"][0]
            self.assertGreater(int(capture["capture_id"]), 0)
            self.assertTrue(all(
                row["capture_id"] == capture["capture_id"]
                for row in imported_graph["tables"]["battle_hit_evidence"]
            ))
            self.assertIsNone(capture["source_inventory_snapshot_id"])

    def test_import_remaps_ids_preserves_facts_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with UserDataDao(root / "source.sqlite3", account_id="source") as source:
                source_id = _insert_summary(source, "portable-operation", 123.0)
                source.promote_battle_record_to_manual(source_id)
                source.save_battle_target_condition(source_id, _target_condition())
                source.update_battle_report_page_state(
                    battle_record_id=source_id,
                    detail_scope="current",
                )
                graph = source.load_battle_report_transfer_rows(source_id)
            assert graph is not None

            with UserDataDao(root / "target.sqlite3", account_id="target") as target:
                local_id = _insert_summary(target, "local-operation", 1.0)
                outcome = target.import_battle_report_transfer_rows([graph])
                imported_id = int(outcome["imported_battle_record_ids"][0])

                self.assertNotEqual(source_id, imported_id)
                self.assertGreater(imported_id, local_id)
                imported = target.load_battle_record(imported_id)
                condition = target.load_battle_target_condition(imported_id)
                self.assertEqual(123.0, imported["raw_summary_payload"]["total_damage"])
                self.assertEqual("测试赐福", condition["witch_buff_name_zh"])
                self.assertEqual("manual", imported["retention_kind"])
                self.assertEqual(local_id, target.restore_battle_report_record()["battle_record_id"])

                repeated = target.import_battle_report_transfer_rows([graph])
                self.assertEqual((), repeated["imported_battle_record_ids"])
                self.assertEqual(1, repeated["skipped_existing_count"])

    def test_stale_before_commit_rolls_back_the_entire_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with UserDataDao(root / "source.sqlite3", account_id="source") as source:
                source_id = _insert_summary(source, "stale-operation", 10.0)
                graph = source.load_battle_report_transfer_rows(source_id)
            assert graph is not None

            with UserDataDao(root / "target.sqlite3", account_id="target") as target:
                with self.assertRaisesRegex(RuntimeError, "stale"):
                    target.import_battle_report_transfer_rows(
                        [graph],
                        before_commit=lambda: (_ for _ in ()).throw(
                            RuntimeError("stale")
                        ),
                    )
                self.assertEqual([], target.list_battle_records())

    def test_conflicting_capture_identity_is_rejected_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with UserDataDao(root / "source.sqlite3", account_id="source") as source:
                source_id = _insert_summary(source, "shared-operation", 200.0)
                graph = source.load_battle_report_transfer_rows(source_id)
            assert graph is not None

            with UserDataDao(root / "target.sqlite3", account_id="target") as target:
                existing_id = _insert_summary(target, "shared-operation", 100.0)
                with self.assertRaisesRegex(
                    UserDataValidationError,
                    "不同战报",
                ):
                    target.import_battle_report_transfer_rows([graph])
                self.assertEqual(100.0, target.load_battle_record(
                    existing_id
                )["raw_summary_payload"]["total_damage"])


if __name__ == "__main__":
    unittest.main()
