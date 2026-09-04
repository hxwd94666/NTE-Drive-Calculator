# 封装战报目标目录读取和用户确认条件的整层保存边界。
"""Target-condition persistence helpers for the battle history facade."""

from __future__ import annotations

from typing import Any

from src.services.battle_outer_realm_confirmation_service import (
    complete_outer_realm_confirmation,
    needs_outer_realm_confirmation,
)
from src.services.battle_report_persistence_service import (
    BattleReportPersistenceDependencies,
)
from src.services.battle_target_catalog_service import BattleTargetCatalogService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataError


class BattleTargetConditionHistoryMixin:
    """Keep catalog-backed confirmation logic outside the history orchestrator."""

    _dependencies: BattleReportPersistenceDependencies
    _target_catalog_cache: dict[str, Any] | None

    def load_target_catalog(self) -> dict[str, Any]:
        if self._target_catalog_cache is not None:
            return self._target_catalog_cache
        static_path = self._dependencies.static_database_path
        if static_path is None:
            raise UserDataError("当前应用没有可用的官方静态数据库")
        with StaticGameDataDao(static_path) as static_dao:
            catalog = BattleTargetCatalogService.load(static_dao)
        self._target_catalog_cache = catalog
        return catalog

    def _complete_target_condition(
        self,
        condition: dict[str, Any] | None,
        evidence: dict[str, Any] | None,
        *,
        require_catalog: bool = False,
    ) -> dict[str, Any] | None:
        if condition is None or not needs_outer_realm_confirmation(
            condition, evidence
        ):
            return condition
        try:
            catalog = self.load_target_catalog()
        except Exception:
            if require_catalog:
                raise
            return condition
        return complete_outer_realm_confirmation(
            condition,
            catalog,
            evidence,
        )

    def save_target_condition(
        self,
        battle_record_id: int,
        condition: dict[str, Any],
    ) -> dict[str, Any]:
        """Save one user-confirmed target input without changing hit evidence."""

        self._assert_counterfactual_editable(battle_record_id)
        with self._open_current_dao() as user_dao:
            evidence = user_dao.load_battle_axis_evidence(battle_record_id)
            saved_condition = self._complete_target_condition(
                condition,
                evidence,
                require_catalog=True,
            )
            assert saved_condition is not None
            return user_dao.save_battle_target_condition(
                battle_record_id,
                saved_condition,
            )
