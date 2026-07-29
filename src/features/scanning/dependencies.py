# 固化一次扫描任务使用的账号路径和应用路径。
"""Immutable path dependencies for one scanning workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.app.context import AppContext


@dataclass(frozen=True)
class ScanningDependencies:
    """Paths pinned when a scan starts, independent of later account switches."""

    account_id: str
    generation: int
    screenshot_dir: Path
    user_config_dir: Path
    user_database_path: Path
    config_dir: Path
    template_dir: Path

    @classmethod
    def from_app_context(cls, app_context: AppContext) -> "ScanningDependencies":
        account = app_context.account
        return cls(
            account_id=account.active_account_id,
            generation=app_context.generation,
            screenshot_dir=account.screenshot_dir,
            user_config_dir=account.user_config_dir,
            user_database_path=account.user_database_path,
            config_dir=app_context.paths.config_dir,
            template_dir=app_context.paths.template_dir,
        )
