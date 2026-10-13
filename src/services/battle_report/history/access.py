# 统一战报历史的账号校验、DAO 生命周期及只读环境列表投影。
"""Account-bound historical access and stored environment projection."""

from __future__ import annotations

import sqlite3
from typing import Any

from src.domain.battle_report import BattleReportHistoryEntry, StoredBattleSummary
from src.services.battle_report.environment.conditions import (
    display_battle_environment_name,
    resolve_battle_target_condition,
)
from src.services.battle_report.targets.inferred_snapshot import (
    BattleInferredTargetSnapshotService,
)
from src.services.battle_report.history.projection import history_entry, stored_summary
from src.services.battle_report.persistence import (
    BattleReportContextGuard,
    BattleReportPersistenceDependencies,
)
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError


class StaleBattleReportContextError(RuntimeError):
    """The requested account dependencies no longer match the active context."""


class BattleReportHistoryDaoMixin:
    _dependencies: BattleReportPersistenceDependencies
    _context_is_current: BattleReportContextGuard

    # 只在 DAO 打开前后复核冻结代次与账号；此门禁不承诺每次查询后的再校验。
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
        # profile 或第二次代次校验失败，也必须释放刚打开的 DAO。
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


def list_history_entries(
    *,
    user_dao: UserDataDao,
    static_dataset_id: str | None = None,
    static_schema_version: int | None = None,
) -> tuple[BattleReportHistoryEntry, ...]:
    """Return stored rows without synchronously inferring old reports."""

    # 历史列表只消费持久化事实与已存派生标签，不触发推断或写入。
    result = []
    for record in user_dao.list_battle_records():
        record_id = int(record["battle_record_id"])
        try:
            condition = resolve_battle_target_condition(
                user_dao.load_battle_target_condition(record_id)
            )
        except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
            condition = None
        # 用户确认环境优先，不能被已存推断覆盖。
        if condition is not None:
            result.append(
                history_entry(
                    record,
                    environment_name=display_battle_environment_name(condition),
                    environment_source="user_confirmed",
                    environment_confidence="高",
                )
            )
            continue

        snapshot = user_dao.load_battle_inferred_target_snapshot(record_id)
        # 可读性检查 schema、非空算法标识及可获得的 dataset 身份，不要求当前算法版本相等。
        if not BattleInferredTargetSnapshotService.is_readable_row(
            snapshot,
            static_dataset_id=static_dataset_id,
            static_schema_version=static_schema_version,
        ):
            snapshot = None
        result.append(
            history_entry(
                record,
                environment_name=(
                    "" if snapshot is None else str(snapshot.get("environment_name") or "")
                ),
                environment_source=("" if snapshot is None else "inferred"),
                environment_confidence=(
                    "" if snapshot is None else str(snapshot.get("confidence") or "")
                ),
            )
        )
    return tuple(result)


__all__ = [
    "BattleReportHistoryDaoMixin",
    "StaleBattleReportContextError",
    "list_history_entries",
]
