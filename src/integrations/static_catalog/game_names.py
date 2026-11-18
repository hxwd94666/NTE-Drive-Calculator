# 只读读取发行静态库中的正式游戏名称，供界面显示投影排除（游戏名称不经界面补丁翻译）。
from __future__ import annotations

import sqlite3
from pathlib import Path

_NAME_SUFFIXES = ("name", "name_zh", "name_cn", "title", "title_zh")


def official_game_names(database_path: str | Path) -> frozenset[str]:
    """读取所有名称列的去重值；库缺失或读取失败返回空集，投影照常但不额外排除。"""
    path = Path(database_path)
    if not path.is_file():
        return frozenset()
    names: set[str] = set()
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return frozenset()
    try:
        tables = [row[0] for row in connection.execute("select name from sqlite_master where type='table'")]
        for table in tables:
            columns = [row[1] for row in connection.execute(f'pragma table_info("{table}")')]
            for column in columns:
                if column.endswith(_NAME_SUFFIXES):
                    for (value,) in connection.execute(f'select distinct "{column}" from "{table}"'):
                        if isinstance(value, str) and value.strip():
                            names.add(value.strip())
    except sqlite3.Error:
        return frozenset()
    finally:
        connection.close()
    return frozenset(names)
