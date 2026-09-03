# 集中提供应用路径、当前账号、数据访问工厂和账号切换生命周期。
"""Explicit application context and account-switch orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.services.account_settings_service import AccountSettingsService
from src.storage.sqlite.shared_data_dao import SharedDataDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True)
class ApplicationPaths:
    """Paths whose ownership does not change when the active account changes."""

    root: Path
    app_dir: Path
    data_root: Path
    bundled_config_dir: Path
    asset_dir: Path
    app_icon_path: Path
    config_dir: Path
    accounts_dir: Path
    accounts_index_file: Path
    shared_database_path: Path
    global_ui_preferences_file: Path
    workshop_weight_template_file: Path
    template_dir: Path
    static_database_path: Path

    @classmethod
    def from_roots(
        cls,
        *,
        root: str | Path,
        app_dir: str | Path,
        data_root: str | Path,
        bundled_config_dir: str | Path,
        asset_dir: str | Path,
        app_icon_path: str | Path,
        static_database_path: str | Path | None = None,
    ) -> "ApplicationPaths":
        root_path = Path(root).resolve()
        data_root_path = Path(data_root).resolve()
        config_dir = data_root_path / "config"
        accounts_dir = data_root_path / "accounts"
        return cls(
            root=root_path,
            app_dir=Path(app_dir).resolve(),
            data_root=data_root_path,
            bundled_config_dir=Path(bundled_config_dir).resolve(),
            asset_dir=Path(asset_dir).resolve(),
            app_icon_path=Path(app_icon_path).resolve(),
            config_dir=config_dir,
            accounts_dir=accounts_dir,
            accounts_index_file=accounts_dir / "accounts.json",
            shared_database_path=data_root_path / "data" / "app_shared.sqlite3",
            global_ui_preferences_file=config_dir / "global_ui_preferences.json",
            workshop_weight_template_file=config_dir / "workshop_weight_template.json",
            template_dir=config_dir / "templates",
            static_database_path=(
                Path(static_database_path).resolve()
                if static_database_path is not None
                else root_path / "data" / "game_static.sqlite3"
            ),
        )


@dataclass(frozen=True)
class AccountContext:
    """All paths and identity values owned by one application account."""

    active_account_id: str
    active_account_name: str
    account_data_root: Path
    user_database_path: Path
    user_config_dir: Path
    screenshot_dir: Path
    log_dir: Path


@dataclass(frozen=True)
class AccountChangedEvent:
    """Immutable account transition delivered after account services rebuild."""

    previous: AccountContext
    current: AccountContext
    generation: int


class AccountLifecycle(Protocol):
    """Account-bound service hooks used by the context switch transaction."""

    def is_running(self) -> bool: ...

    def stop(self) -> None: ...

    def rebuild(self, account: AccountContext) -> None: ...

    def start(self) -> None: ...


class CallbackAccountLifecycle:
    """Adapt explicit callbacks without making AppContext depend on a UI type."""

    def __init__(
        self,
        *,
        is_running: Callable[[], bool],
        stop: Callable[[], None],
        rebuild: Callable[[AccountContext], None],
        start: Callable[[], None],
    ) -> None:
        self._is_running = is_running
        self._stop = stop
        self._rebuild = rebuild
        self._start = start

    def is_running(self) -> bool:
        return bool(self._is_running())

    def stop(self) -> None:
        self._stop()

    def rebuild(self, account: AccountContext) -> None:
        self._rebuild(account)

    def start(self) -> None:
        self._start()


AccountChangedHandler = Callable[[AccountChangedEvent], None]
UserDaoFactory = Callable[..., UserDataDao]
StaticDaoFactory = Callable[..., StaticGameDataDao]
SharedDaoFactory = Callable[..., SharedDataDao]
SettingsFactory = Callable[..., AccountSettingsService]


class AppContext:
    """Own application/account state without exposing an unrestricted locator.

    UI features should receive only a narrow child dependency (for example an
    ``AccountContext`` or one DAO factory).  ``AppContext`` itself belongs at
    the composition root and coordinates account-bound service replacement.
    """

    def __init__(
        self,
        paths: ApplicationPaths,
        account: AccountContext,
        *,
        user_dao_factory: UserDaoFactory = UserDataDao,
        static_dao_factory: StaticDaoFactory = StaticGameDataDao,
        shared_dao_factory: SharedDaoFactory = SharedDataDao,
        settings_factory: SettingsFactory = AccountSettingsService,
    ) -> None:
        self.paths = paths
        self._account = account
        self._generation = 0
        self._user_dao_factory = user_dao_factory
        self._static_dao_factory = static_dao_factory
        self._shared_dao_factory = shared_dao_factory
        self._settings_factory = settings_factory
        self._account_settings = self._build_account_settings(account)
        self._account_changed_handlers: list[AccountChangedHandler] = []
        self._account_lifecycles: list[AccountLifecycle] = []
        self._nte_core_lifecycle: AccountLifecycle | None = None

    @property
    def account(self) -> AccountContext:
        return self._account

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def account_settings(self) -> AccountSettingsService:
        return self._account_settings

    @property
    def nte_core_lifecycle(self) -> AccountLifecycle | None:
        return self._nte_core_lifecycle

    def create_user_dao(self) -> UserDataDao:
        account = self._account
        return self._user_dao_factory(
            account.user_database_path,
            account_id=account.active_account_id,
            account_name=account.active_account_name,
        )

    def create_static_dao(self) -> StaticGameDataDao:
        return self._static_dao_factory(self.paths.static_database_path)

    def create_shared_dao(self) -> SharedDataDao:
        return self._shared_dao_factory(self.paths.shared_database_path)

    def subscribe_account_changed(
        self,
        handler: AccountChangedHandler,
    ) -> Callable[[], None]:
        self._account_changed_handlers.append(handler)

        def unsubscribe() -> None:
            if handler in self._account_changed_handlers:
                self._account_changed_handlers.remove(handler)

        return unsubscribe

    def register_account_lifecycle(
        self,
        lifecycle: AccountLifecycle,
        *,
        nte_core: bool = False,
    ) -> Callable[[], None]:
        if lifecycle not in self._account_lifecycles:
            self._account_lifecycles.append(lifecycle)
        if nte_core:
            if (
                self._nte_core_lifecycle is not None
                and self._nte_core_lifecycle is not lifecycle
            ):
                raise ValueError("nte-core 生命周期服务已经注册")
            self._nte_core_lifecycle = lifecycle

        def unregister() -> None:
            if lifecycle in self._account_lifecycles:
                self._account_lifecycles.remove(lifecycle)
            if self._nte_core_lifecycle is lifecycle:
                self._nte_core_lifecycle = None

        return unregister

    def switch_account(self, account: AccountContext) -> AccountChangedEvent | None:
        """Stop, switch, rebuild, notify and then resume account-bound services."""

        if account.active_account_id == self._account.active_account_id:
            return None

        previous = self._account
        lifecycles = tuple(self._account_lifecycles)
        running_before = {id(item): bool(item.is_running()) for item in lifecycles}

        for lifecycle in lifecycles:
            lifecycle.stop()

        self._account = account
        self._generation += 1
        self._account_settings = self._build_account_settings(account)

        for lifecycle in lifecycles:
            lifecycle.rebuild(account)

        event = AccountChangedEvent(
            previous=previous,
            current=account,
            generation=self._generation,
        )
        for handler in tuple(self._account_changed_handlers):
            handler(event)

        for lifecycle in lifecycles:
            if running_before[id(lifecycle)]:
                lifecycle.start()
        return event

    def _build_account_settings(
        self,
        account: AccountContext,
    ) -> AccountSettingsService:
        return self._settings_factory(
            account.user_database_path,
            legacy_config_dir=account.user_config_dir,
        )
