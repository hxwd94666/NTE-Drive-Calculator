# 固化一次装备鉴定使用的账号路径和应用路径。
"""Immutable path dependencies for one identification workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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
    static_database_path: Path

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
            static_database_path=app_context.paths.equipment_allocation_database_path,
        )


class IdentificationContextOwner(Protocol):
    """只要求显式账号上下文，不依赖窗口类型或其他 Feature。"""

    app_context: AppContext


def current_identification_dependencies(owner: IdentificationContextOwner) -> IdentificationDependencies:
    return IdentificationDependencies.from_app_context(owner.app_context)


def task_identification_dependencies(owner: IdentificationContextOwner) -> IdentificationDependencies:
    dependencies = getattr(owner, "_identify_dependencies", None)
    return dependencies or current_identification_dependencies(owner)
