# 数据根独占租约覆盖启动、业务和维护；操作系统在进程退出时释放锁。
from __future__ import annotations

import os
from pathlib import Path
import stat

from .metadata import DistributionError


def reject_links(path: Path) -> None:
    """检查已有父链，拒绝 junction/symlink 将数据写入另一个归属。"""
    for part in (path, *path.parents):
        if part.is_symlink():
            raise DistributionError(f"数据路径包含链接：{part}")
        try:
            attributes = getattr(part.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            continue
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise DistributionError(f"数据路径包含重解析点：{part}")


class DataRootLease:
    """只协调遵守同一锁协议的进程；旧版迁入仍须显式确认已退出。"""

    def __init__(self, root: Path):
        self.root = Path(os.path.abspath(root))
        reject_links(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / ".ndc-data.lock"
        reject_links(path)
        self._stream = path.open("a+b")
        try:
            if path.stat().st_size == 0:
                self._stream.write(b"\0")
                self._stream.flush()
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._stream.close()
            raise DistributionError(f"数据目录正由其他进程使用：{self.root}；请先退出该程序。") from error

    def close(self) -> None:
        if not self._stream.closed:
            self._stream.close()

    def __enter__(self) -> DataRootLease:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def require_root(self, root: Path) -> None:
        if self._stream.closed or self.root != Path(root).absolute():
            raise DistributionError("数据维护需要持有该数据根的独占租约。")
