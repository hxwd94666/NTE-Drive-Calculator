# 持有语言偏好提交任务并派发冻结参数，视图仅处理控件与提示。
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QObject, Signal

from src.app.page_tasks import PageCommitLane
from src.domain.localization.catalog import CatalogError, TextCatalog
from src.integrations.preferences.json_store import PreferencesError
from src.services.preferences.language_settings_service import LanguageSettingsService


@dataclass(frozen=True)
class LanguageActionResult:
    catalog: TextCatalog | None = None
    error_code: str = ""


def _run_action(action: Callable[[], TextCatalog]) -> LanguageActionResult:
    try:
        return LanguageActionResult(action())
    except CatalogError as exc:
        return LanguageActionResult(error_code=exc.code)
    except PreferencesError:
        return LanguageActionResult(error_code="invalid_preferences")
    except OSError:
        return LanguageActionResult(error_code="file_operation_failed")


class LanguageController(QObject):
    started = Signal()
    completed = Signal(object, bool)
    settled = Signal(bool)

    def __init__(self, service: LanguageSettingsService, parent: QObject) -> None:
        super().__init__(parent)
        self._service = service
        self.selection = service.startup()
        self._closed = False
        self._lane = PageCommitLane(self)
        self._lane.settled.connect(self.settled.emit)

    def choices(self) -> tuple[TextCatalog, ...]:
        return self._service.choices()

    def preferred_locale(self) -> str:
        return self._service.preferred_locale()

    def is_running(self) -> bool:
        return self._lane.is_running()

    def select(self, locale: str) -> None:
        self._submit(partial(self._service.select, locale))

    def restore_default(self) -> None:
        self._submit(self._service.restore_default)

    def install(self, path: str) -> None:
        self._submit(partial(self._service.install, path), installed=True)

    def _submit(self, action: Callable[[], TextCatalog], *, installed: bool = False) -> None:
        if self._closed or self.is_running():
            return
        # worker 只捕获 Service 与本次值；结果确认与控件更新发生在主线程终态。
        if self._lane.submit(partial(_run_action, action),
                             partial(self._completed, installed=installed),
                             self._unexpected_failure, self._unexpected_failure):
            self.started.emit()

    def _completed(self, result: LanguageActionResult, *, installed: bool) -> None:
        self.completed.emit(result, installed)

    def _unexpected_failure(self, _error: str) -> None:
        self.completed.emit(LanguageActionResult(error_code="operation_failed"), False)

    def close(self) -> None:
        # 已派发的原子写不伪装取消；owner 待物理 worker 结束后再销毁。
        self._closed = True
