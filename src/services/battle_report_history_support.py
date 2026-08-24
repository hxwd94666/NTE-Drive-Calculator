# 战报历史 Service 的账号上下文校验与 DAO 生命周期边界。
"""Small lifecycle mixin shared by the battle history facade."""

from __future__ import annotations

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
