# 为仍在使用的计算服务提供只读配置访问。
"""Compatibility DAO for shared calculation helpers.

This module deliberately excludes removed legacy role-cache files and
``role_order.json`` workflows.  It only exposes the immutable role weights and
the editable stat catalog still used by shared calculation services.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.integrations.bundled_resources import bundled_config_dir
from src.storage.json_store import read_json


def load_stats(config_dir: str | Path | None = None) -> dict[str, Any]:
    # 兼容旧角色计算入口；缺省值只允许指向随程序发布的只读配置。
    resolved_config_dir = Path(config_dir) if config_dir is not None else bundled_config_dir()
    stats = read_json(resolved_config_dir / "stats.json", default={}) or {}
    return stats if isinstance(stats, dict) else {}
