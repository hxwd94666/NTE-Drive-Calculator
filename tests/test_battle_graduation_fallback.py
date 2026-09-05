# 验证缺少原生背包时仍冻结毕业配装、保留实测并支持后续分析。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_battle_report_persistence_service import _summary
from tests.test_battle_axis_dao import _equipped_item, _snapshot
from src.observability import OperationContext
from src.services.battle_report_persistence_service import (
    BattleReportPersistenceDependencies, BattleReportPersistenceService,
)
from src.services.battle_report_history_service import BattleReportHistoryService
from src.services.battle_report_transfer_service import BattleReportTransferService
from src.services.battle_marginal_candidate_service import BattleMarginalCandidateService
from src.storage.sqlite.user_data_dao import UserDataDao


class BattleGraduationFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database_path = Path(self.temporary.name) / "user.sqlite3"
        with UserDataDao(self.database_path, account_id="account-a"):
            pass
        self.dependencies = BattleReportPersistenceDependencies(
            account_id="account-a", user_database_path=self.database_path, generation=3,
            static_database_path=Path(__file__).resolve().parents[1]
            / "data" / "game_static.sqlite3",
        )

    def _capture(self):
        service = BattleReportPersistenceService(
            dependencies=self.dependencies, context_is_current=lambda _: True,
            operation_context=OperationContext.create("battle_report"),
        )
        service.begin_capture(
            capture_operation_id="template-fallback",
            captured_at_utc="2026-09-05T00:00:00+00:00",
        )
        service.append_axis_page(capture_operation_id="template-fallback", page={
            "contract_version": 4, "battle_record_id": "battle-test",
            "generation": "1", "complete": True,
            "first_available_cursor": "1", "next_cursor": "2",
            "total_hits": "1", "retained_hits": 1,
            "rows": [{
                "sequence": "1", "timestamp_unix": 1787000000.0,
                "relative_time_seconds": 1.0, "character_id": 1072,
                "character_known": True, "character_name": "测试角色",
                "direction": "outgoing", "damage": 120.0,
                "follow_up_damage": 0.0, "total_damage": 120.0,
                "follow_up_labels": [],
            }],
        })
        return service

    def _finish(self, service):
        return service.finalize_summary(
            capture_operation_id="template-fallback", summary=_summary(),
            raw_summary_payload={"total_damage": 120.0, "total_hits": 12},
            captured_at_utc="2026-09-05T00:00:00+00:00",
            finalized_at_utc="2026-09-05T00:00:10+00:00",
            raw_record_payload={"contract_version": 4, "axis_complete": True},
        )

    def test_missing_inventory_saves_equipment_stats_and_axis_only_role(self):
        outcome = self._finish(self._capture())
        self.assertEqual("saved", outcome.status)
        self.assertIn("毕业模板", outcome.warning_message)
        with UserDataDao(self.database_path, account_id="account-a") as dao:
            build = dao.load_battle_build_snapshot(outcome.battle_record_id)
            evidence = dao.load_battle_axis_evidence(outcome.battle_record_id)
            self.assertIsNone(dao.latest_native_inventory_snapshot_id())
        self.assertIsNone(build["source_inventory_snapshot_id"])
        self.assertEqual({1051, 1072}, {r["character_id"] for r in build["characters"]})
        self.assertEqual(120.0, evidence["hits"][0]["damage"])
        for role in build["characters"]:
            self.assertEqual("official_graduation", role["profile"]["equipment_assumption"]["kind"])
            self.assertEqual({"core", "module"}, {r["kind"] for r in role["equipment"]})
            self.assertTrue(all(r["stats"] for r in role["equipment"]))
            module = next(r for r in role["equipment"] if r["kind"] == "module")
            self.assertTrue(module["graduation_assumed_shape_ids"])
            self.assertTrue(any(r["source_group"] == "equipment" for r in role["stats"]))
            self.assertEqual(0, role["awakening_level"])
        history = BattleReportHistoryService(
            dependencies=self.dependencies, context_is_current=lambda _: True,
        )
        editor = history.load_build_editor_data(outcome.battle_record_id)
        self.assertFalse(editor["equipment_editable"])
        self.assertEqual("local_capture", editor["report_origin"])
        for detail in editor["details"]:
            self.assertIn("毕业模板", detail["equipment_contexts"]["battle"]["title"])
        history.save_build_edit(outcome.battle_record_id, [
            dict(detail["profile"], character_level=70, breakthrough_stage=5)
            for detail in editor["details"]
        ])

    def test_retry_does_not_replace_assumption_with_later_inventory(self):
        service = self._capture()
        first = self._finish(service)
        with UserDataDao(self.database_path, account_id="account-a") as dao:
            original = dao.load_battle_build_snapshot(first.battle_record_id)
            dao.import_inventory_snapshot(_snapshot(1, [_equipped_item(101, 11, 1051)]))
        with patch.object(service, "_load_effective_profiles", side_effect=AssertionError("rebound")):
            retry = self._finish(service)
        with UserDataDao(self.database_path, account_id="account-a") as dao:
            self.assertEqual(original, dao.load_battle_build_snapshot(first.battle_record_id))
        self.assertEqual(first.battle_record_id, retry.battle_record_id)
        self.assertEqual(first.warning_message, retry.warning_message)

    def test_native_inventory_still_takes_priority(self):
        service = self._capture()
        with UserDataDao(self.database_path, account_id="account-a") as dao:
            dao.import_inventory_snapshot(_snapshot(1, [_equipped_item(101, 11, 1051)]))
        outcome = self._finish(service)
        self.assertIsNone(outcome.warning_message)
        with UserDataDao(self.database_path, account_id="account-a") as dao:
            build = dao.load_battle_build_snapshot(outcome.battle_record_id)
        self.assertIsNotNone(build["source_inventory_snapshot_id"])
        for role in build["characters"]:
            self.assertNotIn("equipment_assumption", role["profile"])

    def test_failed_freeze_keeps_staging_for_retry(self):
        service = self._capture()
        with patch.object(service, "_resolve_character_stat_snapshots", side_effect=ValueError("fixture")):
            with self.assertRaisesRegex(ValueError, "fixture"):
                self._finish(service)
        with UserDataDao(self.database_path, account_id="account-a") as dao:
            state = dao.battle_axis_capture_state("template-fallback")
            self.assertEqual("capturing", state["capture_state"])
            self.assertIsNone(state["battle_record_id"])
            self.assertEqual(1, state["stored_hits"])
        self.assertEqual("saved", self._finish(service).status)

    def test_transfer_preserves_assumed_equipment_and_main_stat_candidates(self):
        outcome = self._finish(self._capture())
        with UserDataDao(self.database_path, account_id="account-a") as dao:
            build = dao.load_battle_build_snapshot(outcome.battle_record_id)
            graph = dao.load_battle_report_transfer_rows(outcome.battle_record_id)
        locks = BattleReportTransferService._locked_equipment_at_export(
            frozen_build=build, build_edit=None, import_locks={},
        )
        portable = BattleReportTransferService._prepare_import_row_graph(
            {
                "source_account_nickname": "来源", "export_account_nickname": "导出",
                "database_rows": graph, "locked_equipment_at_export": locks,
            },
            bundle={"bundle_id": "graduation-test"},
            imported_at_utc="2026-09-05T01:00:00+00:00",
        )
        with UserDataDao(self.database_path.parent / "target.sqlite3", account_id="target") as dao:
            imported = dao.import_battle_report_transfer_rows([portable])
            record_id = imported["imported_battle_record_ids"][0]
            copied = dao.load_battle_build_snapshot(record_id)
            for before, after in zip(build["characters"], copied["characters"], strict=True):
                self.assertEqual(before["equipment"], after["equipment"])
                self.assertEqual(before["profile"]["equipment_assumption"], after["profile"]["equipment_assumption"])
            self.assertFalse(dao.battle_report_equipment_editable(record_id))
        candidate = BattleMarginalCandidateService.freeze(
            record_id, [dict(row["profile"], character_id=row["character_id"])
                        for row in copied["characters"]], equipment_editable=False,
        )
        candidate = BattleMarginalCandidateService.with_core_main_stat(
            candidate, 1051, {"property_id": "CritBase", "value": 0.30, "is_percent": True},
        )
        modified = BattleMarginalCandidateService.as_build_edit(candidate, frozen_build=copied)
        role = next(row for row in modified["characters"] if row["character_id"] == 1051)
        core = next(row for row in role["profile"]["equipment_override"] if row["kind"] == "core")
        self.assertEqual(["CritBase"], [r["property_id"] for r in core["stats"] if r["stat_group"] == "main"])
