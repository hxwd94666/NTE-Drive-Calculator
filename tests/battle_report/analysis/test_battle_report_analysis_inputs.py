# 验证候选批次只准备一次原始事实，配置变更与账号切换不会污染共享输入。
"""Frozen analysis facts are request-scoped and isolate candidate mutations."""

from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from src.services.battle_report.build.edit_projection import apply_battle_build_edit
from src.services.battle_report.history.service import BattleReportHistoryService
from src.services.battle_report.history.access import StaleBattleReportContextError
from src.services.battle_report.persistence import BattleReportPersistenceDependencies


class BattleReportAnalysisInputsTests(TestCase):
    def setUp(self) -> None:
        self.guard = Mock(return_value=True)
        self.history = BattleReportHistoryService(
            dependencies=BattleReportPersistenceDependencies(
                account_id="fixture", user_database_path=Path("fixture.sqlite3"), generation=1,
            ),
            context_is_current=self.guard,
        )
        self.dao = Mock()
        self.dao.load_battle_record.return_value = {"id": 7, "raw_summary_payload": {}}
        self.dao.load_battle_axis_evidence.return_value = {
            "hits": [{"character_id": 1004, "damage": 100.0}],
        }
        self.dao.load_battle_build_snapshot.return_value = {
            "characters": [{"character_id": 1004, "character_level": 60,
                            "profile": {"selected_awaken_effect_ids": []}, "stats": []}],
        }
        self.dao.load_battle_build_edit.return_value = {
            "is_active": True,
            "characters": [{"character_id": 1004, "character_level": 80,
                            "breakthrough_stage": 6, "awakening_level": 1,
                            "profile": {"selected_awaken_effect_ids": ["Effect1"]}}],
        }
        self.dao.load_battle_import_equipment_locks.return_value = {}
        self.dao.load_battle_target_condition.return_value = None
        self.dao.load_battle_inferred_target_snapshot.return_value = None
        self.open_dao = self.enterContext(patch.object(
            self.history, "_open_current_dao", side_effect=lambda: nullcontext(self.dao),
        ))
        self.localize = self.enterContext(patch.object(self.history, "_localize_axis_evidence"))
        self.formal = self.enterContext(patch(
            "src.services.battle_report.history.inputs.BattleFormalDamageTagService.project",
        ))

    def test_baseline_and_candidates_share_reads_but_not_mutable_builds(self) -> None:
        frozen = self.history.freeze_analysis_inputs(7)
        _, evidence, edited, saved_edit, _ = frozen.copy_for(self.history, 7)
        apply_battle_build_edit(edited, saved_edit)
        self.assertEqual(80, edited["characters"][0]["character_level"])
        edited["characters"][0]["profile"]["selected_awaken_effect_ids"].append("Effect2")
        evidence["hits"][0]["damage"] = 999.0

        # An inventory/editor update during the batch must only affect the next request.
        self.dao.load_battle_build_snapshot.return_value["characters"][0]["character_level"] = 70
        for _ in range(17):
            _, axis, original, edit, _ = frozen.copy_for(self.history, 7)
            self.assertEqual(60, original["characters"][0]["character_level"])
            self.assertEqual(100.0, axis["hits"][0]["damage"])
            self.assertEqual(["Effect1"], edit["characters"][0]["profile"]["selected_awaken_effect_ids"])
        self.open_dao.assert_called_once_with()
        self.dao.load_battle_axis_evidence.assert_called_once_with(7)
        self.dao.load_battle_build_snapshot.assert_called_once_with(7)
        self.localize.assert_called_once()
        self.formal.assert_called_once()
        new_request = self.history.freeze_analysis_inputs(7)
        self.assertEqual(70, new_request.copy_for(self.history, 7)[2]["characters"][0]["character_level"])

    def test_inputs_cannot_cross_report_or_service(self) -> None:
        frozen = self.history.freeze_analysis_inputs(7)
        with self.assertRaises(ValueError):
            frozen.copy_for(self.history, 8)
        other = BattleReportHistoryService(
            dependencies=self.history._dependencies, context_is_current=self.guard,
        )
        with self.assertRaises(ValueError):
            frozen.copy_for(other, 7)

    def test_candidates_see_new_derived_fit_without_reloading_original_facts(self) -> None:
        frozen = self.history.freeze_analysis_inputs(7)
        before = {"inferred_payload": {"selection_mode": "unfitted"}}
        after = {"inferred_payload": {"selection_mode": "fitted"}}
        self.dao.load_battle_inferred_target_snapshot.side_effect = (before, after)

        class ReachedInference(Exception):
            pass

        with patch(
            "src.services.battle_report.history.service.BattleInferredTargetSnapshotService.resolve",
            side_effect=ReachedInference,
        ) as resolve:
            for _ in range(2):
                with self.assertRaises(ReachedInference):
                    self.history.load_analysis(7, frozen_inputs=frozen, start_us=0, end_us=1)
        self.assertIs(before, resolve.call_args_list[0].kwargs["persisted_row"])
        self.assertIs(after, resolve.call_args_list[1].kwargs["persisted_row"])
        self.dao.load_battle_record.assert_called_once_with(7)
        self.dao.load_battle_axis_evidence.assert_called_once_with(7)
        self.assertEqual(2, self.dao.load_battle_inferred_target_snapshot.call_count)
        self.localize.assert_called_once()

    def test_account_switch_rejects_next_candidate_without_reopening_dao(self) -> None:
        frozen = self.history.freeze_analysis_inputs(7)
        self.guard.return_value = False
        with self.assertRaises(StaleBattleReportContextError):
            self.history.load_analysis(7, frozen_inputs=frozen)
        self.open_dao.assert_called_once_with()

    def test_static_replacement_rejects_next_candidate(self) -> None:
        with TemporaryDirectory() as directory:
            static_path = Path(directory) / "static.sqlite3"
            static_path.write_bytes(b"fixture-v1")
            self.history._dependencies = BattleReportPersistenceDependencies(
                account_id="fixture", user_database_path=Path("fixture.sqlite3"),
                generation=1, static_database_path=static_path,
            )
            frozen = self.history.freeze_analysis_inputs(7)
            static_path.write_bytes(b"fixture-v2-larger")
            with self.assertRaises(StaleBattleReportContextError):
                frozen.copy_for(self.history, 7)

    def test_missing_record_stays_missing_in_same_request(self) -> None:
        self.dao.load_battle_record.return_value = None
        frozen = self.history.freeze_analysis_inputs(7)
        self.dao.load_battle_record.return_value = {"id": 7}
        self.assertIsNone(self.history.load_analysis(7, frozen_inputs=frozen))
        self.open_dao.assert_called_once_with()
        self.localize.assert_not_called()
        self.formal.assert_not_called()
