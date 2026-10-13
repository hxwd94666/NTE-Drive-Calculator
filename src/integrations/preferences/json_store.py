# 有界读取本机 JSON，并以单字段合并和同目录原子替换保留其它偏好。
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from threading import RLock

MAX_PREFERENCES_BYTES = 64 * 1024


class PreferencesError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PreferencesError("duplicate_key")
        result[key] = value
    return result



def _reject_constant(_value):
    raise PreferencesError("invalid_json")


def decode_json(data: bytes) -> object:
    """统一坏 JSON 的异常边界；短文件也可能深层嵌套或包含超长整数。"""
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
                          parse_constant=_reject_constant)
    except PreferencesError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PreferencesError("invalid_json") from exc


def atomic_bytes(path: Path, data: bytes) -> None:
    """只替换明确目标文件；失败不截断原件，并收尾本次临时文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class LocalPreferencesStore:
    """组合根共享一个 owner；同进程读改写串行，不创建账号配置副本。"""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self._lock = RLock()

    def read(self) -> dict[str, object] | None:
        with self._lock:
            try:
                with self.path.open("rb") as stream:
                    data = stream.read(MAX_PREFERENCES_BYTES + 1)
            except FileNotFoundError:
                return None
            if len(data) > MAX_PREFERENCES_BYTES:
                raise PreferencesError("too_large")
            result = decode_json(data)
            if not isinstance(result, dict):
                raise PreferencesError("invalid_schema")
            return result

    def update(self, changes: dict[str, object], *, recover_corrupt: bool = False) -> None:
        with self._lock:
            try:
                previous = self.read() or {}
            except PreferencesError:
                if not recover_corrupt:
                    raise
                previous = {}
            previous.update(changes)
            data = (json.dumps(previous, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
            if len(data) > MAX_PREFERENCES_BYTES:
                raise PreferencesError("too_large")
            atomic_bytes(self.path, data)
