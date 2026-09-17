# 为一次加权分配预览冻结官方角色详情。
"""Freeze official role details without replacing workshop base weights."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from src.services.allocation_context import AllocationContext
from src.services.official_role_page_service import load_official_role_detail


def freeze_official_role_details(
    context: AllocationContext,
    *,
    user_database_path: Path,
    shared_database_path: Path | None,
    static_database_path: Path | None,
) -> tuple[AllocationContext, dict[int, Mapping[str, Any]]]:
    """Freeze role details while preserving account workshop base weights.

    The detail snapshot feeds attribute summaries and direct-damage displays.
    Formal allocation scoring keeps the effective account/template weights
    already frozen in ``AllocationContext``.  Custom roles have no official
    detail and continue using their account-owned base weights.
    """

    details: dict[int, Mapping[str, Any]] = {}
    for role in context.roles:
        try:
            detail = load_official_role_detail(
                user_database_path,
                role.character_id,
                include_inventory_contexts=False,
                static_database_path=static_database_path,
                static_schema_version=context.static_dataset.schema_version,
                shared_database_path=shared_database_path,
            )
        except (OSError, ValueError):
            continue
        details[role.character_id] = detail
    return context, details
