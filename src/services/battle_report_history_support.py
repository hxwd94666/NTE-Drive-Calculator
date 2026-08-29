# 战报历史 Service 的账号上下文校验与 DAO 生命周期边界。
"""Small lifecycle mixin shared by the battle history facade."""

from __future__ import annotations

from typing import Any

from src.domain.battle_report import (
    BattleReportHistoryEntry,
    StoredBattleSummary,
)
from src.services.battle_report_history_projection import (
    stored_summary,
)
from src.services.battle_report_history_entry_service import list_history_entries
from src.services.battle_inferred_target_snapshot_service import (
    BattleInferredTargetSnapshotService,
)

from src.services.battle_report_persistence_service import (
    BattleReportContextGuard,
    BattleReportPersistenceDependencies,
)
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError


class StaleBattleReportContextError(RuntimeError):
    """The requested account dependencies no longer match the active context."""


class BattleReportHistoryDaoMixin:
    _dependencies: BattleReportPersistenceDependencies
    _context_is_current: BattleReportContextGuard

    def _open_current_dao(self) -> UserDataDao:
        dependencies = self._dependencies
        if not self._context_is_current(dependencies):
            raise StaleBattleReportContextError("战报账号上下文已经变化")
        if not dependencies.user_database_path.is_file():
            raise UserDataError("冻结账号的用户数据库不存在")
        user_dao = UserDataDao(
            dependencies.user_database_path,
            account_id=dependencies.account_id,
            account_name=dependencies.account_id,
        )
        try:
            context_matches = (
                str(user_dao.profile()["account_id"]) == dependencies.account_id
                and self._context_is_current(dependencies)
            )
        except Exception:
            user_dao.close()
            raise
        if not context_matches:
            user_dao.close()
            raise StaleBattleReportContextError("战报账号上下文已经变化")
        return user_dao

    def list_records(self) -> list[dict[str, Any]]:
        with self._open_current_dao() as user_dao:
            return user_dao.list_battle_records()

    def list_entries(self) -> tuple[BattleReportHistoryEntry, ...]:
        static_dataset_id, static_schema_version = (
            BattleInferredTargetSnapshotService.static_identity(
                self._dependencies.static_database_path
            )
        )
        with self._open_current_dao() as user_dao:
            return list_history_entries(
                user_dao=user_dao,
                static_dataset_id=static_dataset_id,
                static_schema_version=static_schema_version,
            )

    def load_record(self, battle_record_id: int) -> dict[str, Any] | None:
        with self._open_current_dao() as user_dao:
            return user_dao.load_battle_record(battle_record_id)

    def restore_last_record(self) -> dict[str, Any] | None:
        with self._open_current_dao() as user_dao:
            return user_dao.restore_battle_report_record()

    def restore_last_summary(self) -> StoredBattleSummary | None:
        record = self.restore_last_record()
        return None if record is None else stored_summary(record)

    def load_summary(self, battle_record_id: int) -> StoredBattleSummary | None:
        record = self.load_record(battle_record_id)
        return None if record is None else stored_summary(record)
