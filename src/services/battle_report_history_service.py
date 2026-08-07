# 通过窄服务边界读取和管理账号战报历史。
"""Read and manage account battle history through a narrow service boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

from src.domain.battle_report import (
    BattleReportHistoryEntry,
    BattleRetentionMutation,
    StoredBattleSummary,
)
from src.integrations.nte_core_battle import parse_battle_summary
from src.services.battle_report_persistence_service import (
    BattleReportContextGuard,
    BattleReportPersistenceDependencies,
)
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError


class StaleBattleReportContextError(RuntimeError):
    """The requested account dependencies no longer match the active context."""


class BattleReportHistoryService:
    def __init__(
        self,
        *,
        dependencies: BattleReportPersistenceDependencies,
        context_is_current: BattleReportContextGuard,
    ) -> None:
        self._dependencies = BattleReportPersistenceDependencies(
            account_id=str(dependencies.account_id),
            user_database_path=Path(dependencies.user_database_path).resolve(),
            generation=int(dependencies.generation),
        )
        self._context_is_current = context_is_current

    def list_records(self) -> list[dict[str, Any]]:
        with self._open_current_dao() as user_dao:
            return user_dao.list_battle_records()

    def list_entries(self) -> tuple[BattleReportHistoryEntry, ...]:
        return tuple(self._history_entry(record) for record in self.list_records())

    def load_record(self, battle_record_id: int) -> dict[str, Any] | None:
        with self._open_current_dao() as user_dao:
            return user_dao.load_battle_record(battle_record_id)

    def restore_last_record(self) -> dict[str, Any] | None:
        with self._open_current_dao() as user_dao:
            return user_dao.restore_battle_report_record()

    def restore_last_summary(self) -> StoredBattleSummary | None:
        record = self.restore_last_record()
        return None if record is None else self._stored_summary(record)

    def load_summary(self, battle_record_id: int) -> StoredBattleSummary | None:
        record = self.load_record(battle_record_id)
        return None if record is None else self._stored_summary(record)

    def save_record(self, battle_record_id: int) -> BattleRetentionMutation:
        with self._open_current_dao() as user_dao:
            result = user_dao.promote_battle_record_to_manual(battle_record_id)
        return self._retention_mutation(result)

    def unmark_record(self, battle_record_id: int) -> BattleRetentionMutation:
        with self._open_current_dao() as user_dao:
            result = user_dao.unmark_manual_battle_record(battle_record_id)
        return self._retention_mutation(result)

    def delete_record(self, battle_record_id: int) -> bool:
        with self._open_current_dao() as user_dao:
            return user_dao.delete_battle_record(battle_record_id)

    def update_page_state(
        self,
        *,
        battle_record_id: int | None,
        detail_scope: str,
    ) -> dict[str, Any]:
        with self._open_current_dao() as user_dao:
            return user_dao.update_battle_report_page_state(
                battle_record_id=battle_record_id,
                detail_scope=detail_scope,
            )

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

    @staticmethod
    def _stored_summary(record: dict[str, Any]) -> StoredBattleSummary:
        scope = str(record.get("restored_detail_scope") or "current")
        if scope not in {"current", "first", "second"}:
            scope = "current"
        retention_kind = str(record["retention_kind"])
        if retention_kind not in {"auto", "manual"}:
            raise RuntimeError("战报保留状态无效")
        return StoredBattleSummary(
            battle_record_id=int(record["battle_record_id"]),
            retention_kind=cast(Literal["auto", "manual"], retention_kind),
            saved_at_utc=str(record["saved_at_utc"]),
            detail_scope=cast(
                Literal["current", "first", "second"],
                scope,
            ),
            summary=parse_battle_summary(record["raw_summary_payload"]),
        )

    @staticmethod
    def _history_entry(record: dict[str, Any]) -> BattleReportHistoryEntry:
        retention_kind = str(record["retention_kind"])
        if retention_kind not in {"auto", "manual"}:
            raise RuntimeError("战报保留状态无效")
        context_kind = str(record["combat_context_kind"])
        if context_kind not in {"abyss", "non_abyss"}:
            raise RuntimeError("战报上下文状态无效")
        floor = record["abyss_floor"]
        return BattleReportHistoryEntry(
            battle_record_id=int(record["battle_record_id"]),
            retention_kind=cast(Literal["auto", "manual"], retention_kind),
            saved_at_utc=str(record["saved_at_utc"]),
            combat_context_kind=cast(
                Literal["abyss", "non_abyss"],
                context_kind,
            ),
            abyss_floor=None if floor is None else int(floor),
            has_first_half=bool(record["has_first_half"]),
            has_second_half=bool(record["has_second_half"]),
            character_ids=tuple(int(item) for item in record["character_ids"]),
            total_damage=float(record["total_damage"]),
            total_dps=float(record["total_dps"]),
            duration_seconds=float(record["duration_seconds"]),
            total_hits=int(record["total_hits"]),
            capability_level=str(record["capability_level"]),
            source_kind=str(record["source_kind"]),
        )

    @staticmethod
    def _retention_mutation(result: dict[str, Any]) -> BattleRetentionMutation:
        record = result["record"]
        retention_kind = str(record["retention_kind"])
        if retention_kind not in {"auto", "manual"}:
            raise RuntimeError("战报保留状态无效")
        return BattleRetentionMutation(
            battle_record_id=int(record["battle_record_id"]),
            retention_kind=cast(Literal["auto", "manual"], retention_kind),
            changed=bool(result["changed"]),
            pruned_battle_record_ids=tuple(
                int(item) for item in result.get("pruned_battle_record_ids", ())
            ),
        )
