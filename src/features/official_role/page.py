# 提供官方角色页的公共组合与离页入口，沿用既有页面和 Controller。
"""页面入口负责展示投递/草稿选择，不接管角色业务或任务物理收尾。"""

from __future__ import annotations

from .role_shell import (
    _page_my_role,
    _refresh_my_role,
    confirm_pending_my_role_changes,
)


def build_official_role_page(window):
    return _page_my_role(window)


def refresh_official_role_page(
    window, *, restore_scroll_value: int | None = None, discard_pending: bool = False,
):
    """仅显式 discard_pending 丢弃草稿；普通刷新继续沿用角色页公共入口。"""

    if discard_pending:
        window._official_role_dirty_ids = set()
        window._official_role_world_bonus_dirty = False
        window._my_role_dirty = False
        window._official_role_editors = {}
    return _refresh_my_role(window, restore_scroll_value=restore_scroll_value, force=discard_pending)


def deactivate_official_role_page(window):
    """离页撤销读取投递与构建 token，保留草稿和有界缓存；不把 token 失效当作线程已退场。"""
    controller = getattr(window, "_official_role_controller", None)
    if controller is not None:
        controller.cancel_reads()
    tabs = getattr(window, "official_role_tabs", None)
    if tabs is not None:
        for index in range(tabs.count()):
            tabs.widget(index)._role_build_token = None


__all__ = [
    "build_official_role_page",
    "refresh_official_role_page",
    "confirm_pending_my_role_changes",
    "deactivate_official_role_page",
]
