# 统一 JSON 文件的 UTF-8 读写、目录创建和原子保存。
"""Small JSON persistence helpers; not a database abstraction."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def read_json(path: str | Path, default: Any = None) -> Any:
    json_path = Path(path)
    if not json_path.exists():
        return default
    with open(json_path, "r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: str | Path, data: Any, indent: int = 2) -> None:
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=indent)


def write_json_atomic(path: str | Path, data: Any, indent: int = 2) -> None:
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = json_path.with_name(f"{json_path.name}.tmp")
    write_json(tmp_path, data, indent=indent)
    tmp_path.replace(json_path)


def backup_json(path: str | Path, suffix: str = ".bak") -> Path | None:
    json_path = Path(path)
    if not json_path.exists():
        return None
    backup_path = json_path.with_name(f"{json_path.name}{suffix}")
    shutil.copy2(json_path, backup_path)
    return backup_path
