# 固化基础权重页面使用的账号、代次、用户库和静态配置目录。
"""Immutable dependencies for the basic-weight vertical slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.app.context import AppContext


@dataclass(frozen=True, slots=True)
class BasicWeightDependencies:
    account_id: str
    generation: int
    user_database_path: Path
    config_dir: Path
    static_database_path: Path
    shared_database_path: Path

    @classmethod
    def from_app_context(
        cls, app_context: AppContext
    ) -> "BasicWeightDependencies":
        account = app_context.account
        return cls(
            account_id=account.active_account_id,
            generation=app_context.generation,
            user_database_path=account.user_database_path,
            config_dir=app_context.paths.config_dir,
            static_database_path=app_context.paths.static_database_path,
            shared_database_path=app_context.paths.shared_database_path,
        )
