# 为仍在使用的计算服务提供只读配置访问。
"""Compatibility DAO for shared calculation helpers.

This module deliberately excludes the removed ``my_roles.json`` and
``role_order.json`` workflows.  It only exposes the immutable role weights and
the editable stat catalog still used by shared calculation services.
"""

from __future__ import annotations

from typing import Any

from src.app import runtime
from src.storage.json_store import read_json


def load_role(role_name: str) -> dict[str, Any]:
    roles = read_json(runtime.CONFIG_DIR / "roles.json", default={}) or {}
    entry = roles.get(role_name, {}) if isinstance(roles, dict) else {}
    return entry if isinstance(entry, dict) else {}


def load_stats() -> dict[str, Any]:
    stats = read_json(runtime.CONFIG_DIR / "stats.json", default={}) or {}
    return stats if isinstance(stats, dict) else {}
