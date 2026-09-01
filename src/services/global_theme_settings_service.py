# 持久化跨账号共享的应用主题偏好。
"""Application-scoped theme preference persistence."""

from __future__ import annotations

from pathlib import Path

from src.services.global_ui_preferences_store import GlobalUiPreferencesStore


THEME_PREFERENCES = frozenset({"dark", "black", "light"})


def _normalized_theme(value: object, default: str = "black") -> str:
    theme = str(value or "").strip()
    return theme if theme in THEME_PREFERENCES else default


class GlobalThemeSettingsService:
    """Read and atomically write the one theme shared by every account."""

    def __init__(self, settings_path: str | Path) -> None:
        self._store = GlobalUiPreferencesStore(settings_path)

    @property
    def settings_path(self) -> Path:
        return self._store.settings_path

    def load(self, *, legacy_theme: object = None) -> str:
        value = self._store.read()
        if value is not None:
            return _normalized_theme(value.get("theme"))
        theme = _normalized_theme(legacy_theme)
        self.save(theme)
        return theme

    def save(self, theme: object) -> str:
        normalized = _normalized_theme(theme)
        self._store.update(theme=normalized)
        return normalized
