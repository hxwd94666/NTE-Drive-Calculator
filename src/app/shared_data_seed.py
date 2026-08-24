# 初始化公共兼容数据库；官方额外形状不再从这里读取。
"""Install packaged compatibility shared data over the local copy."""

from __future__ import annotations

import shutil
from pathlib import Path


def seed_shared_database(
    bundled_database_path: str | Path,
    data_root: str | Path,
) -> Path:
    """Replace the local public database with the packaged release default."""

    destination = Path(data_root) / "data" / "app_shared.sqlite3"
    source = Path(bundled_database_path)
    if source.is_file() and source.resolve() != destination.resolve():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return destination
