# 统一 JSON 文件的 UTF-8 读写、目录创建和原子保存。
"""Small JSON persistence helpers; not a database abstraction."""

from __future__ import annotations

import json
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
