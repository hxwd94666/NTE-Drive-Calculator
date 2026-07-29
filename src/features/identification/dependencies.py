# 固化一次装备鉴定使用的账号路径和应用路径。
"""Immutable path dependencies for one identification workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.app.context import AppContext


@dataclass(frozen=True)
class IdentificationDependencies:
    """Paths pinned when an identification operation starts."""

    account_id: str
    generation: int
    account_data_root: Path
    screenshot_dir: Path
    user_config_dir: Path
    user_database_path: Path
    config_dir: Path

    @classmethod
    def from_app_context(
        cls,
        app_context: AppContext,
    ) -> "IdentificationDependencies":
        account = app_context.account
        return cls(
            account_id=account.active_account_id,
            generation=app_context.generation,
            account_data_root=account.account_data_root,
            screenshot_dir=account.screenshot_dir,
            user_config_dir=account.user_config_dir,
            user_database_path=account.user_database_path,
            config_dir=app_context.paths.config_dir,
        )
