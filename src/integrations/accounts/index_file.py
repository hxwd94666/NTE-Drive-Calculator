# 串行本进程账号索引字节读写，以同目录临时文件原子替换。
from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

_INDEX_LOCK = RLock()


@contextmanager
def locked_account_index() -> Iterator[None]:
    """索引锁先于账号 DAO 写锁；临界区中不等待任务、弹窗或调用组件。"""
    # 只保证同进程参与者串行，字节比较不是跨进程 CAS，也不保证掉电原子性。
    with _INDEX_LOCK:
        yield


def read_account_index_bytes(path: str | Path) -> bytes:
    with locked_account_index():
        return Path(path).read_bytes()


def write_account_index_bytes(path: str | Path, raw: bytes) -> None:
    target = Path(path)
    with locked_account_index():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{target.name}.", suffix=".tmp",
                                             dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
