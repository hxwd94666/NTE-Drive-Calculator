# 仅在原生输入身份一致时复用少量压缩页面结果，不保存可变展示对象。
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import zlib


MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_RAW_BYTES = 64 * 1024 * 1024


def file_identity(path: Path) -> tuple:
    path = path.resolve()
    stat = path.stat()
    return str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino


def page_cache_key(payload: dict, *, input_digest: str, files: tuple,
                   engine_version: str, dataset_version: str) -> bytes:
    # Map insertion order is semantic input; do not sort nested candidates.
    value = [payload, input_digest, files, engine_version, dataset_version]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                    separators=(',', ':')).encode('utf-8')).digest()


class BattleNativePageCache:
    """Two LRU entries at most, bounded by their combined compressed size."""

    def __init__(self, *, max_bytes: int = MAX_COMPRESSED_BYTES):
        self._max_bytes = max_bytes
        self._record_id: int | None = None
        self._entries: OrderedDict[bytes, bytes] = OrderedDict()
        self._size = 0

    def select_record(self, record_id: int) -> None:
        if record_id != self._record_id:
            self._entries.clear()
            self._size = 0
            self._record_id = record_id

    @property
    def compressed_bytes(self) -> int:
        return self._size

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def get(self, key: bytes) -> dict | None:
        packed = self._entries.get(key)
        if packed is None:
            return None
        self._entries.move_to_end(key)
        # Entries are exclusively produced here from validated native results.
        return json.loads(zlib.decompress(packed))

    def discard(self, key: bytes) -> None:
        packed = self._entries.pop(key, None)
        if packed is not None:
            self._size -= len(packed)

    def put(self, key: bytes, raw: dict) -> None:
        payload = json.dumps(raw, ensure_ascii=False, allow_nan=False,
                             separators=(',', ':')).encode('utf-8')
        if len(payload) > MAX_RAW_BYTES:
            return
        packed = zlib.compress(payload, level=1)
        if len(packed) > self._max_bytes:
            return
        previous = self._entries.pop(key, None)
        if previous is not None:
            self._size -= len(previous)
        while self._entries and (len(self._entries) >= 2 or self._size + len(packed) > self._max_bytes):
            _old_key, old = self._entries.popitem(last=False)
            self._size -= len(old)
        self._entries[key] = packed
        self._size += len(packed)
