# 持久化跨账号共享的应用主题偏好。
"""Application-scoped theme preference persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from src.utils.logger import logger


THEME_PREFERENCES = frozenset({"dark", "black", "light"})


def _normalized_theme(value: object, default: str = "black") -> str:
    theme = str(value or "").strip()
    return theme if theme in THEME_PREFERENCES else default


class GlobalThemeSettingsService:
    """Read and atomically write the one theme shared by every account."""

    def __init__(self, settings_path: str | Path) -> None:
        self.settings_path = Path(settings_path).expanduser().resolve()

    def load(self, *, legacy_theme: object = None) -> str:
        value = self._read()
        if value is not None:
            return _normalized_theme(value.get("theme"))
        theme = _normalized_theme(legacy_theme)
        self.save(theme)
        return theme

    def save(self, theme: object) -> str:
        normalized = _normalized_theme(theme)
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
                json.dump({"theme": normalized}, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.settings_path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return normalized

    def _read(self) -> dict[str, object] | None:
        if not self.settings_path.is_file():
            return None
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                f"读取全局主题设置失败，将恢复默认主题: {self.settings_path.name} | {exc}"
            )
            return None
        if not isinstance(value, dict):
            logger.warning(
                f"读取全局主题设置失败，内容不是对象: {self.settings_path.name}"
            )
            return None
        return value
