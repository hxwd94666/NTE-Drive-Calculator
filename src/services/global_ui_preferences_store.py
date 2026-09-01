# 原子读写跨账号共享的界面偏好文件，保留未知键。
"""Atomic read/write for the application-scoped UI preferences file.

Theme and language live in one file, so every write is a read-modify-write that
preserves keys the writing service does not own.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from src.utils.logger import logger


class GlobalUiPreferencesStore:
    """Read and atomically update the preferences shared by every account."""

    def __init__(self, settings_path: str | Path) -> None:
        self.settings_path = Path(settings_path).expanduser().resolve()

    def read(self) -> dict[str, object] | None:
        if not self.settings_path.is_file():
            return None
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                f"读取全局界面偏好失败，将恢复默认值: {self.settings_path.name} | {exc}"
            )
            return None
        if not isinstance(value, dict):
            logger.warning(
                f"读取全局界面偏好失败，内容不是对象: {self.settings_path.name}"
            )
            return None
        return value

    def update(self, **values: object) -> dict[str, object]:
        """Merge ``values`` into the stored payload and write it atomically."""
        payload = dict(self.read() or {})
        payload.update(values)
        self._write(payload)
        return payload

    def _write(self, payload: dict[str, object]) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=f".{self.settings_path.name}.",
                suffix=".tmp",
                dir=str(self.settings_path.parent),
            )
            temporary_path = Path(raw_path)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.settings_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
