# 解析已保存配装展示所需的当前账号数据库、静态库和资源目录。
"""Narrow path boundary shared by saved-equipment display components."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def equipment_paths(window) -> tuple[Path, Path, Path]:
    context = getattr(window, "app_context", None)
    if context is None:
        database_path = getattr(window, "user_database_path", None)
        static_database_path = getattr(window, "static_database_path", None)
        asset_dir = getattr(window, "asset_dir", None)
        if (
            database_path is None
            or static_database_path is None
            or asset_dir is None
        ):
            raise RuntimeError("配装展示缺少 AppContext 或显式路径依赖")
        return (
            Path(database_path),
            Path(static_database_path),
            Path(asset_dir),
        )
    return (
        Path(context.account.user_database_path),
        Path(context.paths.static_database_path),
        Path(context.paths.asset_dir),
    )


def equipment_presentation(window) -> Any:
    """Return the explicitly composed shared equipment presentation view."""

    presentation = getattr(window, "equipment_presentation", None)
    if presentation is None:
        raise RuntimeError("配装展示缺少公开的 equipment presentation 依赖")
    return presentation
