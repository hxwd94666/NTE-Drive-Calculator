# 验证最终战报持久化、账号防护和历史服务。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.domain.battle_report import (
    BattleAbyssSummary,
    BattleCharacterSummary,
    BattleQualitySummary,
    BattleSummary,
)
from src.observability import OperationContext
from src.services.battle_report_persistence_service import (
    BattleReportPersistenceDependencies,
    BattleReportPersistenceService,
)
from src.services.battle_report_history_service import (
    BattleReportHistoryService,
    StaleBattleReportContextError,
)
from src.services.battle_inferred_target_condition_service import (
    INFERRED_ENCOUNTER_ALGORITHM_VERSION,
)
from src.storage.sqlite.user_data_dao import UserDataDao


def _summary(
    *,
    total_damage: float = 120.0,
    total_hits: int = 12,
    max_hp_reduction: float = 0.0,
) -> BattleSummary:
    character = BattleCharacterSummary(
        character_id=1051,
        name="测试角色",
        hits=total_hits,
        damage=total_damage,
        dps=12.0,
        damage_share_percent=100.0,
    )
    return BattleSummary(
        duration_seconds=10.0,
        dps_time_mode="subtract_time_stop",
        total_damage=total_damage,
        total_dps=12.0,
        total_damage_taken=0.0,
        total_hits=total_hits,
        characters=(character,),
        skills=(),
        abyss=BattleAbyssSummary(detected=False),
        quality=BattleQualitySummary(),
        max_hp_reduction=max_hp_reduction,
    )


class BattleReportPersistenceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "user_data.sqlite3"
        with UserDataDao(
            self.database_path,
            account_id="account-a",
            account_name="账号 A",
        ):
            pass
        self.dependencies = BattleReportPersistenceDependencies(
            account_id="account-a",
            user_database_path=self.database_path,
            generation=3,
        )
        self.operation = OperationContext.create(
            "battle_report",
            account_id="account-a",
            context_generation=3,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _service(self, *, current: bool = True) -> BattleReportPersistenceService:
        return BattleReportPersistenceService(
            dependencies=self.dependencies,
            context_is_current=lambda _dependencies: current,
            operation_context=self.operation,
        )

    def test_freeze_resolves_legacy_graduation_fork_stage_explicitly(self) -> None:
        class FakeStaticDao:
            @staticmethod
            def list_fork_templates():
                return [{
                    "fork_id": "fork_Rose",
                    "breakthroughs": [
                        {"stage": 5, "max_fork_level": 70},
                        {"stage": 6, "max_fork_level": 80},
                    ],
                }]

            @staticmethod
            def list_character_graduation_templates():
                return [{
                    "character_id": 1004,
                    "profile": {
                        "fork_id": "fork_Rose",
                        "fork_level": 70,
                    },
                }]

            @staticmethod
            def list_character_awaken_effects(_character_id):
                return []

        class FakeUserDao:
            @staticmethod
            def list_character_profiles(*, include_inactive):
                self.assertTrue(include_inactive)
                return []

        with patch(
            "src.services.battle_report_persistence_service."
            "static_character_shape_profile_fields",
            return_value={},
        ):
            profiles = BattleReportPersistenceService._load_effective_profiles(
                static_dao=FakeStaticDao(),
                user_dao=FakeUserDao(),
            )

        self.assertEqual(5, profiles[1004]["fork_breakthrough_stage"])

    def test_final_summary_is_saved_with_complete_raw_payload(self) -> None:
        payload = {
            "total_damage": 120.0,
            "max_hp_reduction": 30.0,
            "total_hits": 12,
            "abyss": {"detected": False, "floor": None},
            "quality": {"abyss_event_count": 4},
        }
        outcome = self._service().finalize_summary(
            raw_summary_payload=payload,
            summary=_summary(max_hp_reduction=30.0),
            capture_operation_id=self.operation.operation_id,
            captured_at_utc="2026-08-07T00:00:00+00:00",
            finalized_at_utc="2026-08-07T00:00:10+00:00",
            nte_core_provenance={
                "core_version": "0.4.3",
                "protocol_version": 1,
                "data_version": "1",
                "executable_sha256": "A" * 64,
            },
        )

        self.assertEqual("saved", outcome.status)
        self.assertIsNotNone(outcome.battle_record_id)
        with UserDataDao(self.database_path) as user_dao:
            record = user_dao.load_battle_record(int(outcome.battle_record_id or 0))
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(payload, record["raw_summary_payload"])
        self.assertEqual((1051,), record["character_ids"])
        self.assertNotIn("max_hp_reduction", record)
        self.assertEqual("0.4.3", record["nte_core_version"])
        self.assertEqual(1, record["nte_core_protocol_version"])
        self.assertEqual("1", record["nte_core_data_version"])
        self.assertEqual("A" * 64, record["nte_core_executable_sha256"])

    def test_empty_summary_is_not_persisted(self) -> None:
        outcome = self._service().finalize_summary(
            raw_summary_payload={"total_damage": 0, "total_hits": 0},
            summary=_summary(total_damage=0.0, total_hits=0),
            capture_operation_id=self.operation.operation_id,
            captured_at_utc="2026-08-07T00:00:00+00:00",
            finalized_at_utc="2026-08-07T00:00:01+00:00",
        )

        self.assertEqual("skipped_empty", outcome.status)
        with UserDataDao(self.database_path) as user_dao:
            self.assertEqual([], user_dao.list_battle_records())

    def test_stale_generation_is_discarded_before_database_write(self) -> None:
        outcome = self._service(current=False).finalize_summary(
            raw_summary_payload={"total_damage": 120.0, "total_hits": 12},
            summary=_summary(),
            capture_operation_id=self.operation.operation_id,
            captured_at_utc="2026-08-07T00:00:00+00:00",
            finalized_at_utc="2026-08-07T00:00:01+00:00",
        )

        self.assertEqual("discarded_stale", outcome.status)
        with UserDataDao(self.database_path) as user_dao:
            self.assertEqual([], user_dao.list_battle_records())

    def test_history_service_projects_entries_and_toggles_retention(self) -> None:
        outcome = self._service().finalize_summary(
            raw_summary_payload={
                "total_damage": 120.0,
                "total_hits": 12,
                "characters": [
                    {
                        "char_id": 1051,
                        "name": "测试角色",
                        "hits": 12,
                        "damage": 120.0,
                        "dps": 12.0,
                        "damage_share_percent": 100.0,
                    }
                ],
                "abyss": {"detected": False},
            },
            summary=_summary(),
            capture_operation_id=self.operation.operation_id,
            captured_at_utc="2026-08-07T00:00:00+00:00",
            finalized_at_utc="2026-08-07T00:00:01+00:00",
        )
        history = BattleReportHistoryService(
            dependencies=self.dependencies,
            context_is_current=lambda _dependencies: True,
        )

        entries = history.list_entries()
        saved = history.save_record(int(outcome.battle_record_id or 0))
        unmarked = history.unmark_record(int(outcome.battle_record_id or 0))
        stored = history.load_summary(int(outcome.battle_record_id or 0))

        self.assertEqual(1, len(entries))
        self.assertEqual("non_abyss", entries[0].combat_context_kind)
        self.assertEqual((1051,), entries[0].character_ids)
        self.assertEqual("manual", saved.retention_kind)
        self.assertEqual("auto", unmarked.retention_kind)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(120.0, stored.summary.total_damage)

    def test_history_list_reads_persisted_inference_without_recomputing(self) -> None:
        outcome = self._service().finalize_summary(
            raw_summary_payload={
                "total_damage": 120.0,
                "total_hits": 12,
                "abyss": {"detected": False},
            },
            summary=_summary(),
            capture_operation_id=self.operation.operation_id,
            captured_at_utc="2026-08-07T00:00:00+00:00",
            finalized_at_utc="2026-08-07T00:00:01+00:00",
        )
        dependencies = BattleReportPersistenceDependencies(
            account_id="account-a",
            user_database_path=self.database_path,
            generation=3,
            static_database_path=Path(self.temporary.name) / "game_static.sqlite3",
        )
        history = BattleReportHistoryService(
            dependencies=dependencies,
            context_is_current=lambda _dependencies: True,
        )
        record_id = int(outcome.battle_record_id or 0)
        with UserDataDao(self.database_path) as user_dao:
            user_dao.save_battle_inferred_target_snapshot(
                battle_record_id=record_id,
                payload_schema_version=1,
                algorithm_version=INFERRED_ENCOUNTER_ALGORITHM_VERSION,
                static_dataset_id="dataset-a",
                static_schema_version=29,
                inference_status="resolved",
                environment_kind="open_world",
                environment_ref="anomaly:black-book:80",
                environment_name="异象追猎 · 黑之书 · Lv.80",
                source_kind="inferred_encounter_hp_injective_default",
                confidence="高",
                inferred_payload={"environment_name": "异象追猎 · 黑之书 · Lv.80"},
            )

        with patch(
            "src.services.battle_inferred_target_condition_service."
            "BattleInferredTargetConditionService.infer",
        ) as infer:
            first = history.list_entries()

        self.assertEqual(record_id, first[0].battle_record_id)
        self.assertEqual("异象追猎 · 黑之书 · Lv.80", first[0].environment_name)
        self.assertEqual("inferred", first[0].environment_source)
        self.assertEqual("高", first[0].environment_confidence)
        infer.assert_not_called()

    def test_history_service_rejects_stale_context(self) -> None:
        history = BattleReportHistoryService(
            dependencies=self.dependencies,
            context_is_current=lambda _dependencies: False,
        )

        with self.assertRaises(StaleBattleReportContextError):
            history.list_entries()


if __name__ == "__main__":
    unittest.main()
