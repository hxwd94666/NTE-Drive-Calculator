# 初始化公共兼容数据库；官方额外形状不再从这里读取。
"""显式启动时安装发行公共兼容库，不读取账号运行库。"""

from __future__ import annotations

import shutil
from pathlib import Path


def seed_shared_database(
    bundled_database_path: str | Path,
    data_root: str | Path,
) -> Path:
    """只为首次使用提供基线；本机兼容迁移状态归 DAO，不随发行覆盖。"""

    destination = Path(data_root) / "data" / "app_shared.sqlite3"
    source = Path(bundled_database_path)
    if source.is_file() and not destination.exists() and source.resolve() != destination.resolve():
        destination.parent.mkdir(parents=True, exist_ok=True)
        # 启动持有数据根锁；暂存完整后原子发布，不让中断留下半份基线。
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".shared-seed-", delete=False) as stream:
            temporary = Path(stream.name)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination
