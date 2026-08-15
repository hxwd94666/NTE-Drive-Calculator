# 固化一次扫描任务使用的账号路径和应用路径。
"""Immutable path dependencies for one scanning workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def current_scanning_dependencies(owner: Any) -> ScanningDependencies:
    return ScanningDependencies.from_app_context(owner.app_context)


def task_scanning_dependencies(owner: Any) -> ScanningDependencies:
    dependencies = getattr(owner, "_scan_dependencies", None)
    return dependencies or current_scanning_dependencies(owner)


def scanning_dependencies_are_current(
    owner: Any,
    dependencies: ScanningDependencies,
) -> bool:
    app_context = getattr(owner, "app_context", None)
    if app_context is None:
        return True
    account = app_context.account
    return (
        app_context.generation == dependencies.generation
        and account.active_account_id == dependencies.account_id
        and account.user_database_path == dependencies.user_database_path
    )
