# 组装与当前账号绑定的战报服务依赖。
"""Composition helpers for account-bound battle report services."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QWidget

from src.app.context import AppContext
from src.app.constants import APP_VERSION
from src.features.battle_report.controller import BattleReportController
from src.integrations.nte_core import NteCoreClient
from src.observability import OperationContext
from src.services.battle_report_history_service import BattleReportHistoryService
from src.services.battle_report_transfer_service import (
    BattleReportTransferDependencies,
    BattleReportTransferService,
)
from src.services.battle_report_persistence_service import (
    BattleReportPersistenceDependencies,
    BattleReportPersistenceService,
)


class BattleReportServiceFactory:
    def __init__(self, app_context: AppContext) -> None:
        self._app_context = app_context

    def persistence_service(
        self,
        dependencies: BattleReportPersistenceDependencies,
        operation_context: OperationContext,
    ) -> BattleReportPersistenceService:
        return BattleReportPersistenceService(
            dependencies=dependencies,
            context_is_current=self._context_is_current,
            operation_context=operation_context,
        )

    def history_service(
        self,
        dependencies: BattleReportPersistenceDependencies,
    ) -> BattleReportHistoryService:
        return BattleReportHistoryService(
            dependencies=dependencies,
            context_is_current=self._context_is_current,
        )

    def transfer_service(self) -> BattleReportTransferService:
        account = self._app_context.account
        persistence_dependencies = BattleReportPersistenceDependencies(
            account_id=account.active_account_id,
            user_database_path=account.user_database_path,
            generation=self._app_context.generation,
            static_database_path=self._app_context.paths.static_database_path,
        )
        dependencies = BattleReportTransferDependencies(
            account_id=account.active_account_id,
            generation=self._app_context.generation,
            user_database_path=account.user_database_path,
            accounts_index_path=self._app_context.paths.accounts_index_file,
            static_database_path=self._app_context.paths.static_database_path,
            static_manifest_path=(
                self._app_context.paths.static_database_path.parent / "manifest.json"
            ),
            application_version=APP_VERSION,
        )
        return BattleReportTransferService(
            dependencies=dependencies,
            context_is_current=self._transfer_context_is_current,
            history_service=self.history_service(persistence_dependencies),
        )

    def _context_is_current(
        self,
        dependencies: BattleReportPersistenceDependencies,
    ) -> bool:
        account = self._app_context.account
        return (
            self._app_context.generation == dependencies.generation
            and account.active_account_id == dependencies.account_id
            and account.user_database_path.resolve()
            == dependencies.user_database_path.resolve()
        )

    def _transfer_context_is_current(
        self,
        dependencies: BattleReportTransferDependencies,
    ) -> bool:
        account = self._app_context.account
        return (
            self._app_context.generation == dependencies.generation
            and account.active_account_id == dependencies.account_id
            and account.user_database_path.resolve()
            == dependencies.user_database_path.resolve()
            and self._app_context.paths.static_database_path.resolve()
            == dependencies.static_database_path.resolve()
        )


def build_battle_report_controller(
    *,
    app_context: AppContext,
    dialog_parent: QWidget,
    inventory_sync_is_running: Callable[[], bool],
    stop_inventory_sync: Callable[[], None],
    start_inventory_sync: Callable[[], None],
) -> BattleReportController:
    service_factory = BattleReportServiceFactory(app_context)
    return BattleReportController(
        app_context=app_context,
        dialog_parent=dialog_parent,
        inventory_sync_is_running=inventory_sync_is_running,
        stop_inventory_sync=stop_inventory_sync,
        start_inventory_sync=start_inventory_sync,
        client_factory=lambda data_dir: NteCoreClient(
            data_dir=data_dir,
            cwd=app_context.paths.app_dir,
        ),
        persistence_factory=service_factory.persistence_service,
        history_factory=service_factory.history_service,
        transfer_factory=service_factory.transfer_service,
    )
