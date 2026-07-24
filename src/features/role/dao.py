# 为仍在使用的计算服务提供只读配置访问。
"""Compatibility DAO for shared calculation helpers.

This module deliberately excludes removed legacy role-cache files and
``role_order.json`` workflows.  It only exposes the immutable role weights and
the editable stat catalog still used by shared calculation services.
"""

from __future__ import annotations

from typing import Any

from src.storage.json_store import read_json

def load_stats() -> dict[str, Any]:
    from src.app import runtime

    stats = read_json(runtime.CONFIG_DIR / "stats.json", default={}) or {}
    return stats if isinstance(stats, dict) else {}
