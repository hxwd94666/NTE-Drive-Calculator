# 为基础权重页面生成有界输入指纹，支持后台缓存失效而不触碰 Qt。
from __future__ import annotations

from pathlib import Path

from src.domain.basic_weight import BasicWeightDependencies


def basic_weight_source_key(
    dependencies: BasicWeightDependencies, template: Path | None,
) -> tuple[tuple, ...]:
    """仅检查业务依赖文件；WAL、模板和配置变动均使旧投影失效。"""
    files = [dependencies.config_dir / "stats.json"]
    for database in (dependencies.user_database_path, dependencies.static_database_path):
        files.extend((database, Path(str(database) + "-wal")))
    if template is not None:
        files.append(template)
    stamps = []
    for path in files:
        try:
            state = path.stat()
            stamps.append((str(path), state.st_ino, state.st_size, state.st_mtime_ns))
        except OSError:
            stamps.append((str(path), None))
    return tuple(stamps)
