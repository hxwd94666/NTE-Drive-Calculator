# 角色页面兼容入口。
"""Thin composition entry point for official role feature."""

from __future__ import annotations

from .role_shell import (
    _page_my_role,
    _refresh_my_role,
    confirm_pending_my_role_changes,
)


def build_official_role_page(window):
    return _page_my_role(window)


def refresh_official_role_page(window, *, restore_scroll_value: int | None = None):
    """Refresh the role page through its public feature entry point."""

    return _refresh_my_role(window, restore_scroll_value=restore_scroll_value)


__all__ = [
    "build_official_role_page",
    "refresh_official_role_page",
    "confirm_pending_my_role_changes",
]
