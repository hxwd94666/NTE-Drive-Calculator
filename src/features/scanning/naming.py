# 集中定义扫描截图文件命名规则和解析范围判断。
"""Naming helpers for full and incremental scan screenshots."""

from __future__ import annotations

import re
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}
FULL_SCAN_PREFIX = "raw_drive_"
PROBE_PREFIX = "raw_drive_probe_"
AUTO_NEW_PREFIX = "raw_drive_new_"
SEMI_PREFIX = "raw_drive_semi_"
INCREMENTAL_PREFIXES = (PROBE_PREFIX, AUTO_NEW_PREFIX, SEMI_PREFIX)
AUTO_INCREMENTAL_PREFIXES = (PROBE_PREFIX, AUTO_NEW_PREFIX)
SEMI_INCREMENTAL_PREFIXES = (SEMI_PREFIX,)
FAILED_SCREENSHOT_DIRNAME = "failed"
FULL_SCAN_TEMP_DIRNAME = "temp"


def numbered_filename(prefix: str, index: int, suffix: str = ".png") -> str:
    return f"{prefix}{int(index):04d}{suffix or '.png'}"


def full_scan_filename(index: int, suffix: str = ".png") -> str:
    return numbered_filename(FULL_SCAN_PREFIX, index, suffix)


def probe_filename(index: int, suffix: str = ".png") -> str:
    return numbered_filename(PROBE_PREFIX, index, suffix)


def auto_new_filename(index: int, suffix: str = ".png") -> str:
    return numbered_filename(AUTO_NEW_PREFIX, index, suffix)


def semi_filename(index: int, suffix: str = ".png") -> str:
    return numbered_filename(SEMI_PREFIX, index, suffix)


def stem_and_suffix(filename: str | Path) -> tuple[str, str]:
    path = Path(filename)
    return path.stem, path.suffix.lower()


def raw_drive_index_from_name(filename: str | Path) -> int | None:
    stem, _suffix = stem_and_suffix(filename)
    match = re.fullmatch(r"raw_drive_(\d+)", stem)
    return int(match.group(1)) if match else None


def is_image_filename(filename: str | Path) -> bool:
    _stem, suffix = stem_and_suffix(filename)
    return bool(suffix) and suffix in IMAGE_EXTS


def is_full_scan_filename(filename: str | Path) -> bool:
    stem, suffix = stem_and_suffix(filename)
    return (not suffix or suffix in IMAGE_EXTS) and re.fullmatch(r"raw_drive_\d+", stem) is not None


def is_incremental_filename(filename: str | Path, parse_scope: str = "incremental") -> bool:
    stem, suffix = stem_and_suffix(filename)
    if suffix and suffix not in IMAGE_EXTS:
        return False
    if parse_scope == "incremental_auto":
        return stem.startswith(AUTO_INCREMENTAL_PREFIXES)
    if parse_scope == "incremental_semi":
        return stem.startswith(SEMI_INCREMENTAL_PREFIXES)
    return stem.startswith(INCREMENTAL_PREFIXES)
