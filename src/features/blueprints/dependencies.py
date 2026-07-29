# 固化角色图纸工作线程启动时的账号身份和上下文代次。
"""Immutable dependencies for one blueprint generation request."""

from __future__ import annotations

from dataclasses import dataclass

from src.app.context import AppContext


@dataclass(frozen=True, slots=True)
class BlueprintDependencies:
    account_id: str
    generation: int

    @classmethod
    def from_app_context(cls, app_context: AppContext) -> "BlueprintDependencies":
        return cls(
            account_id=app_context.account.active_account_id,
            generation=app_context.generation,
        )

