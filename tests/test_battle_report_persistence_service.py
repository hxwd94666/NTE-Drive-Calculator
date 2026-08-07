# 验证最终战报持久化、账号防护和历史服务。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
from src.storage.sqlite.user_data_dao import UserDataDao


def _summary(*, total_damage: float = 120.0, total_hits: int = 12) -> BattleSummary:
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

    def test_final_summary_is_saved_with_complete_raw_payload(self) -> None:
        payload = {
            "total_damage": 120.0,
            "total_hits": 12,
            "abyss": {"detected": False, "floor": None},
            "quality": {"abyss_event_count": 4},
        }
        outcome = self._service().finalize_summary(
            raw_summary_payload=payload,
            summary=_summary(),
            capture_operation_id=self.operation.operation_id,
            captured_at_utc="2026-08-07T00:00:00+00:00",
            finalized_at_utc="2026-08-07T00:00:10+00:00",
        )

        self.assertEqual("saved", outcome.status)
        self.assertIsNotNone(outcome.battle_record_id)
        with UserDataDao(self.database_path) as user_dao:
            record = user_dao.load_battle_record(int(outcome.battle_record_id or 0))
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(payload, record["raw_summary_payload"])
        self.assertEqual((1051,), record["character_ids"])

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

    def test_history_service_rejects_stale_context(self) -> None:
        history = BattleReportHistoryService(
            dependencies=self.dependencies,
            context_is_current=lambda _dependencies: False,
        )

        with self.assertRaises(StaleBattleReportContextError):
            history.list_entries()


if __name__ == "__main__":
    unittest.main()
