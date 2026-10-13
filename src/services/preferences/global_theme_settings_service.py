# 维护跨账号主题，只更新 theme 字段并保留语言等其它本机偏好。
from __future__ import annotations

from pathlib import Path

from src.integrations.preferences.json_store import LocalPreferencesStore, PreferencesError
from src.utils.logger import logger

THEME_PREFERENCES = frozenset({"dark", "black", "light"})


def _normalized_theme(value: object, default: str = "black") -> str:
    theme = str(value or "").strip()
    return theme if theme in THEME_PREFERENCES else default


class GlobalThemeSettingsService:
    def __init__(self, settings_path: str | Path, *, store: LocalPreferencesStore | None = None):
        self.store = store if store is not None else LocalPreferencesStore(settings_path)
        self.settings_path = self.store.path

    def load(self, *, legacy_theme: object = None) -> str:
        try:
            value = self.store.read()
        except PreferencesError as exc:
            # 仅记格式错误码，不将 JSON 原文或异常载荷写入日志。
            logger.warning(f"读取全局主题设置失败，将恢复默认主题: {type(exc).__name__}")
            theme = _normalized_theme(legacy_theme)
            self.store.update({"theme": theme}, recover_corrupt=True)
            return theme
        # locale-only 配置不能误判主题已迁移，仍沿用旧账号主题的首次迁移规则。
        if value is not None and "theme" in value:
            return _normalized_theme(value["theme"])
        theme = _normalized_theme(legacy_theme)
        self.store.update({"theme": theme})
        return theme

    def save(self, theme: object) -> str:
        normalized = _normalized_theme(theme)
        self.store.update({"theme": normalized})
        return normalized
