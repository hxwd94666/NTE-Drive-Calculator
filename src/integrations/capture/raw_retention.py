# 按采集 owner 指定目录执行原始 pcapng 诊断文件的保留清理。
"""原始诊断抓包文件 IO；账号归属、调用时点及业务事件仍由采集 owner 管理。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path



RAW_CAPTURE_SUFFIX = ".pcapng"
DEFAULT_RAW_CAPTURE_RETAIN_COUNT = 5
DEFAULT_RAW_CAPTURE_MAX_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RawCapturePruneResult:
    deleted_count: int
    deleted_bytes: int
    retained_count: int
    retained_bytes: int


def prune_raw_capture_files(
    directory: str | Path,
    *,
    retain_count: int = DEFAULT_RAW_CAPTURE_RETAIN_COUNT,
    max_total_bytes: int = DEFAULT_RAW_CAPTURE_MAX_BYTES,
) -> RawCapturePruneResult:
    """仅清理指定目录直属的 pcapng 文件，不递归或扩大到其他诊断文件。

    最新文件即使独自超过容量预算也保留，避免丢失当前排查证据。
    后续文件同时受数量和累计大小限制；删除失败的文件计入实际保留量。
    """
    if retain_count < 1:
        raise ValueError("retain_count must be at least 1")
    if max_total_bytes < 1:
        raise ValueError("max_total_bytes must be positive")

    root = Path(directory).expanduser()
    if not root.is_dir():
        return RawCapturePruneResult(0, 0, 0, 0)

    # 目录由原采集 owner 提供；本层不发现账号目录，也不读取抓包内容。
    captures = []
    for path in root.iterdir():
        try:
            if not path.is_file() or path.suffix.casefold() != RAW_CAPTURE_SUFFIX:
                continue
            stat = path.stat()
        except OSError:
            continue
        captures.append((path, int(stat.st_mtime_ns), int(stat.st_size)))
    captures.sort(key=lambda row: (row[1], row[0].name), reverse=True)

    kept: list[tuple[Path, int, int]] = []
    deleted_count = 0
    deleted_bytes = 0
    total_bytes = 0
    for index, (path, modified_at, size) in enumerate(captures):
        del modified_at
        keep_for_count = index < retain_count
        keep_for_size = total_bytes + size <= max_total_bytes
        # 最新文件始终保留；时间相同仍按原文件名逆序决定先后。
        if index == 0 or (keep_for_count and keep_for_size):
            kept.append((path, 0, size))
            total_bytes += size
            continue
        try:
            path.unlink()
        except OSError:
            # 结束时文件可能仍被占用；保留并等待原 owner 下次清理，不中断采集。
            kept.append((path, 0, size))
            total_bytes += size
        else:
            deleted_count += 1
            deleted_bytes += size

    return RawCapturePruneResult(
        deleted_count=deleted_count,
        deleted_bytes=deleted_bytes,
        retained_count=len(kept),
        retained_bytes=total_bytes,
    )
