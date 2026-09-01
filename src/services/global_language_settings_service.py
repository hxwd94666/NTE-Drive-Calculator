# 持久化跨账号共享的界面语言偏好。
"""Application-scoped interface language preference persistence."""

from __future__ import annotations

from pathlib import Path

from src.i18n import DEFAULT_LANGUAGE, normalized_language
from src.services.global_ui_preferences_store import GlobalUiPreferencesStore


class GlobalLanguageSettingsService:
    """Read and atomically write the one language shared by every account."""

    def __init__(self, settings_path: str | Path) -> None:
        self._store = GlobalUiPreferencesStore(settings_path)

    @property
    def settings_path(self) -> Path:
        return self._store.settings_path

    def load(self) -> str:
        value = self._store.read()
        if value is None:
            return DEFAULT_LANGUAGE
        return normalized_language(value.get("language"))

    def save(self, language: object) -> str:
        normalized = normalized_language(language)
        self._store.update(language=normalized)
        return normalized
