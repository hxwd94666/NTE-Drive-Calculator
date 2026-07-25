"""Retention policy for nte-core raw ``.pcapng`` diagnostic captures."""

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
    """Keep recent nte-core captures without touching unrelated diagnostics.

    The newest capture is always retained, even if it alone exceeds the size
    budget.  This avoids deleting the file currently being investigated while
    still removing old packet traces as soon as a later capture is created.
    """
    if retain_count < 1:
        raise ValueError("retain_count must be at least 1")
    if max_total_bytes < 1:
        raise ValueError("max_total_bytes must be positive")

    root = Path(directory).expanduser()
    if not root.is_dir():
        return RawCapturePruneResult(0, 0, 0, 0)

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
        # Never discard the newest file; it may be the only useful diagnostic.
        if index == 0 or (keep_for_count and keep_for_size):
            kept.append((path, 0, size))
            total_bytes += size
            continue
        try:
            path.unlink()
        except OSError:
            # A capture may still be flushing while the sync thread is ending.
            # Leave it for the next successful sync instead of failing capture.
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
