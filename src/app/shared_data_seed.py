# 初始化公共兼容数据库；官方额外形状不再从这里读取。
"""显式启动时安装发行公共兼容库，不读取账号运行库。"""

from __future__ import annotations

import shutil
from pathlib import Path


def seed_shared_database(
    bundled_database_path: str | Path,
    data_root: str | Path,
) -> Path:
    """复制发行公共库到指定数据域；源与目标同路径时保留原文件。"""

    destination = Path(data_root) / "data" / "app_shared.sqlite3"
    source = Path(bundled_database_path)
    if source.is_file() and source.resolve() != destination.resolve():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return destination
