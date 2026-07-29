# 导出词条配装页面构建、刷新和不可变结果渲染入口。
"""Explicit public entry points for weighted allocation."""

from __future__ import annotations

from .weighted_result_view import render_weighted_allocation_result
from .weighted_shell import (
    build_weighted_allocation_page,
    refresh_weighted_allocation_page,
)


__all__ = [
    "build_weighted_allocation_page",
    "refresh_weighted_allocation_page",
    "render_weighted_allocation_result",
]
