# 从应用上下文提取词条配装所需的账号路径、资源目录和代次。
"""Narrow explicit dependencies for the weighted-allocation feature."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.app.context import AppContext
from src.services.account_settings_service import AccountSettingsService


@dataclass(frozen=True, slots=True)
class WeightedAllocationDependencies:
    """Account-scoped values pinned when a calculation or UI action starts."""

    account_id: str
    generation: int
    user_database_path: Path
    game_ui_asset_root: Path
    account_settings: AccountSettingsService

    @classmethod
    def from_context(
        cls,
        context: AppContext,
    ) -> "WeightedAllocationDependencies":
        return cls(
            account_id=context.account.active_account_id,
            generation=context.generation,
            user_database_path=Path(context.account.user_database_path),
            game_ui_asset_root=Path(context.paths.asset_dir) / "game_ui",
            account_settings=context.account_settings,
        )


class AppContextHost(Protocol):
    app_context: AppContext


def weighted_allocation_dependencies(
    window: AppContextHost,
) -> WeightedAllocationDependencies:
    """Resolve dependencies at the point of use, never from runtime mirrors."""

    context = getattr(window, "app_context", None)
    if not isinstance(context, AppContext):
        raise RuntimeError("词条配装缺少 AppContext 依赖")
    return WeightedAllocationDependencies.from_context(context)
