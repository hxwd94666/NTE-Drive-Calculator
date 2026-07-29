# 迁移基础 JSON 配置，补齐新版缺失字段但保留用户已有值。
"""Core configuration migration helpers."""

from __future__ import annotations

import shutil
from pathlib import Path


def replace_core_config_dir(
    user_config_dir: str | Path,
    bundled_config_dir: str | Path,
    core_config_files: tuple[str, ...],
) -> int:
    """Replace versioned scoring catalogs instead of preserving stale values.

    ``stats.json`` defines the shipped OCR/scoring vocabulary and must stay in
    lockstep with the executable.  It is not a user-preference file, so unlike
    legacy settings it intentionally has no merge or backup path.
    """

    user_config_dir = Path(user_config_dir)
    bundled_config_dir = Path(bundled_config_dir)
    replaced = 0
    for filename in core_config_files:
        source = bundled_config_dir / filename
        destination = user_config_dir / filename
        if not source.is_file() or source.resolve() == destination.resolve():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source), str(destination))
        replaced += 1
    return replaced
