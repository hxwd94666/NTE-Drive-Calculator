# 持有计算目录的后台校验、最新请求与账号生命周期。
from __future__ import annotations

from PySide6.QtCore import QObject, Qt

from src.app.page_tasks import PageRequest, PageTaskLane
from src.services.allocation.catalog import AllocationCatalogService, CatalogDependencies
from src.utils.logger import logger


class AllocationCatalogController(QObject):
    def __init__(self, window, dependencies):
        super().__init__(window)
        self.window, self.dependencies = window, dependencies
        self.service = AllocationCatalogService(dependencies, window._read_allocation_catalog)
        self.lane = PageTaskLane(self)
        self._epoch = 0
        self._closed = False
        self._reload_priority = False
        self._continuation = None
        self._verify = False

    def is_writing(self):
        return False

    def invalidate(self):
        self._epoch += 1
        self.lane.cancel()
        self._continuation = None
        self._verify = True
        self._set_ready(False)

    def _current(self):
        return not self._closed and self.dependencies == CatalogDependencies.from_context(self.window.app_context)

    def _set_ready(self, ready):
        window = self.window
        readiness = getattr(window, "set_allocation_catalog_ready", None)
        if callable(readiness):
            readiness(ready)
        if hasattr(window, "btn_run"):
            window.btn_run.setEnabled(ready and not window.scanning_controller.is_running())
            window.btn_run.setToolTip("" if ready else "正在更新计算数据，请稍候。")
        selector = window.scanning_controller.role_selector
        if hasattr(selector, "setEnabled"):
            selector.setEnabled(ready)

    def refresh(self, *, reload_priority=False, continuation=None, verify=False):
        if not self._current() or self.window.scanning_controller.is_running():
            return
        self._reload_priority |= reload_priority
        # 显式计算续接不能被普通刷新替换；仍等待本次目录校验。
        if continuation is not None and self._continuation is None:
            self._continuation = continuation
        self._set_ready(False)
        key = self.dependencies, self._epoch, bool(verify or self._verify or self._continuation)

        def apply(result):
            if not self._current() or self.window.scanning_controller.is_running():
                return
            loaded, semantic, rebuilt, summary = result
            if (rebuilt or self._reload_priority
                    or getattr(self.window, "_allocation_catalog_loaded_key", None) != semantic):
                self.window._apply_allocation_catalog(loaded, reload_priority=self._reload_priority, source_key=semantic)
            self._reload_priority = False
            self._verify = False
            self.window._apply_inventory_status(summary)
            self._set_ready(True)
            continuation, self._continuation = self._continuation, None
            if continuation is not None:
                # 校验期间已离开计算页时，不启动隐藏计算。
                stack = getattr(self.window, "stack", None)
                if stack is None or self.window._nav_key_for_index(stack.currentIndex()) == "execute":
                    continuation()

        def failed(error):
            if self._current():
                self._continuation = None
                self._set_ready(False)
                if hasattr(self.window, "btn_run"):
                    self.window.btn_run.setToolTip("计算数据更新失败，请重新进入计算页重试。")
                logger.warning(f"allocation.catalog_refresh_failed | {error}")

        def read():
            return (*self.service.read(verify=key[-1]), self.service.inventory_summary())
        self.lane.submit(PageRequest(key, read, apply, failed))

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._continuation = None
        self.lane.close()
        if self.lane.is_running():
            self.lane.idle.connect(self.service.clear, Qt.ConnectionType.SingleShotConnection)
        else:
            self.service.clear()


def allocation_catalog_controller(window):
    dependencies = CatalogDependencies.from_context(window.app_context)
    previous = getattr(window, "allocation_catalog_controller", None)
    if previous is None or previous._closed or previous.dependencies != dependencies:
        if previous is not None:
            previous.close()
        previous = AllocationCatalogController(window, dependencies)
        window.allocation_catalog_controller = previous
    return previous
