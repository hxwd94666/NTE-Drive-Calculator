# 替换随发行版本维护的评分词表；该目录输入不是账号显式偏好合并。
"""Core configuration migration helpers."""

from __future__ import annotations

import shutil
from pathlib import Path


def replace_core_config_dir(
    user_config_dir: str | Path,
    bundled_config_dir: str | Path,
    core_config_files: tuple[str, ...],
) -> int:
    """替换版本化评分目录，不沿用旧发行词表或与用户偏好合并。

    stats.json 是与程序同版的 OCR/评分词汇，不是用户偏好。
    仅处理调用方给定的目录文件；同路径或缺发行文件时保持原状。
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
