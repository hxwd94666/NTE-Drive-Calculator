# 固化角色页面一次操作使用的账号身份、代次和数据库路径。
"""Immutable account dependencies for the official-role vertical slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.app.context import AppContext


@dataclass(frozen=True, slots=True)
class OfficialRoleDependencies:
    """Account-scoped inputs captured before loading or saving role data."""

    account_id: str
    generation: int
    user_database_path: Path
    static_database_path: Path
    shared_database_path: Path
    asset_root: Path | None = None
    catalog_identity: str = ""

    @classmethod
    def from_app_context(
        cls, app_context: AppContext
    ) -> "OfficialRoleDependencies":
        account = app_context.account
        catalog = app_context.paths.role_catalog
        return cls(
            account_id=account.active_account_id,
            generation=app_context.generation,
            user_database_path=account.user_database_path,
            static_database_path=catalog.database_path if catalog else app_context.paths.static_database_path,
            shared_database_path=app_context.paths.shared_database_path,
            asset_root=catalog.asset_root if catalog else None,
            catalog_identity=f"{catalog.dataset_id}:{catalog.sha256}" if catalog else "",
        )
