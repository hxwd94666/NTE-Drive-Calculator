# 统一 JSON 文件的 UTF-8 读写、目录创建和原子保存。
"""Small JSON persistence helpers; not a database abstraction."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: str | Path, default: Any = None) -> Any:
    json_path = Path(path)
    if not json_path.exists():
        return default
    with open(json_path, "r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: str | Path, data: Any, indent: int = 2) -> None:
    # 序列化及 UTF-8 校验先完成；坏输入连新目录和暂存都不创建。
    payload = json.dumps(data, ensure_ascii=False, indent=indent)
    payload.encode("utf-8")
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        # 同目录独占创建，刷新成功后才替换；写入失败保留原配置并向上传播。
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=json_path.parent,
            prefix=f".{json_path.name}.", suffix=".tmp", delete=False,
        ) as file:
            temporary_path = Path(file.name)
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, json_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
