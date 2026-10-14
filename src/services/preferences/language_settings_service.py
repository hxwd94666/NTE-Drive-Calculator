# 编排本机语言偏好、完整补丁安装与下次启动生效，不参与账号或计算状态。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from src.domain.localization.catalog import BASE_LOCALE, CatalogError, TextCatalog, valid_locale
from src.integrations.preferences.json_store import LocalPreferencesStore, PreferencesError
from src.integrations.preferences.locale_files import LocaleFiles


@dataclass(frozen=True)
class LanguageSelection:
    catalog: TextCatalog
    requested_locale: str
    warning_code: str = ""


class LanguageSettingsService:
    def __init__(self, preferences: LocalPreferencesStore, files: LocaleFiles):
        self.preferences = preferences
        self.files = files
        self._active: LanguageSelection | None = None
        self._choices: tuple[TextCatalog, ...] = ()
        self._preferred = BASE_LOCALE
        self._lock = RLock()

    def startup(self) -> LanguageSelection:
        """只在运行期首次取得完整目录；保存选择不热改当前页面或账号。"""
        with self._lock:
            if self._active is None:
                warning = ""
                try:
                    preference = self.preferences.read() or {}
                    value = preference.get("locale", BASE_LOCALE)
                except PreferencesError:
                    value, warning = BASE_LOCALE, "invalid_preferences"
                locale = value if valid_locale(value) else BASE_LOCALE
                if locale != value:
                    warning = "invalid_locale"
                try:
                    catalog = self.files.load(locale)
                except CatalogError as exc:
                    catalog, warning = self.files.base, exc.code
                try:
                    self._choices = self.files.available()
                except CatalogError as exc:
                    self._choices, warning = (self.files.base,), exc.code
                self._preferred = locale
                self._active = LanguageSelection(catalog, locale, warning)
            return self._active

    def preferred_locale(self) -> str:
        with self._lock:
            self.startup()
            return self._preferred

    def choices(self) -> tuple[TextCatalog, ...]:
        with self._lock:
            self.startup()
            return self._choices

    def select(self, locale: str) -> TextCatalog:
        with self._lock:
            catalog = self.files.load(locale)
            self.preferences.update({"locale": catalog.locale})
            self._preferred = catalog.locale
            return catalog

    def install(self, source: str | Path) -> TextCatalog:
        # 安装与选择是两个明确动作；安装失败不动偏好或当前目录。
        with self._lock:
            catalog = self.files.install(source)
            self._choices = tuple(row for row in self.choices() if row.locale != catalog.locale) + (catalog,)
            return catalog
