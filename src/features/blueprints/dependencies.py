# 固化角色图纸工作线程启动时的账号身份和上下文代次。
"""Immutable dependencies for one blueprint generation request."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.app.context import AppContext


@dataclass(frozen=True, slots=True)
class BlueprintDependencies:
    account_id: str
    generation: int
    user_database_path: Path
    static_database_path: Path
    shared_database_path: Path

    @classmethod
    def from_app_context(cls, app_context: AppContext) -> "BlueprintDependencies":
        return cls(
            account_id=app_context.account.active_account_id,
            generation=app_context.generation,
            user_database_path=Path(app_context.account.user_database_path),
            static_database_path=Path(app_context.paths.static_database_path),
            shared_database_path=Path(app_context.paths.shared_database_path),
        )

