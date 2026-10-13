# 管理工作台摘要后台读取、版本去重和隐藏页面的刷新合并。
"""有界只读摘要刷新；同步瞬时状态由独立投影负责。"""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any

from PySide6.QtCore import QObject, QTimer

from src.app.page_tasks import PageRequest, PageTaskLane
from src.services.accounts.dashboard import DashboardService
from src.utils.logger import logger
from src.utils.perf import log_perf


@dataclass(frozen=True)
class DashboardDependencies:
    generation: int
    user_database_path: Path
    static_database_path: Path


class DashboardController(QObject):
    """最多一个活动读取和一个待处理修订；隐藏页面不启动数据库读取。"""

    def __init__(
        self, *, dependencies: Callable[[], DashboardDependencies],
        apply: Callable[[dict[str, Any]], None], failed: Callable[[str], None],
        loading: Callable[[], None], parent: QObject | None = None,
        merge_ms: int = 500,
    ) -> None:
        super().__init__(parent)
        self._dependencies = dependencies
        self._apply = apply
        self._failed = failed
        self._loading = loading
        self._merge_ms = merge_ms
        self._reads = PageTaskLane(self)
        self._reads.idle.connect(self._schedule)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start)
        self._identity: DashboardDependencies | None = None
        self._revision = 0
        self._dirty = True
        self._visible = False
        self._closed = False
        self._last_started = 0.0
        self._version: Hashable | None = None
        self._retry_blocked = False

    def _check_identity(self) -> None:
        identity = self._dependencies()
        if identity != self._identity:
            self._reads.cancel()
            self._timer.stop()
            self._identity = identity
            self._revision += 1
            self._version = None
            self._dirty = True
            self._retry_blocked = False
            self._last_started = 0.0
            self._loading()

    def set_visible(self, visible: bool) -> None:
        if self._closed:
            return
        self._check_identity()
        if visible and not self._visible:
            self._last_started = 0.0
        self._visible = visible
        if not visible:
            self._timer.stop()
            if self._reads.is_running():
                self._reads.cancel()
                self._dirty = True
                self._revision += 1
        else:
            self._schedule()

    def refresh(self, *, version: Hashable | None = None) -> None:
        """无版本只用于已知变动或显式刷新，不因同步状态跳动重复读库。"""
        if self._closed:
            return
        self._check_identity()
        if version is not None and version == self._version:
            return
        self._version = version
        self._revision += 1
        self._dirty = True
        self._retry_blocked = False
        # 已运行结果不得覆盖新修订；取消交付由现有读取 lane 管理。
        self._reads.cancel()
        self._schedule()

    def _schedule(self) -> None:
        if self._closed or not self._visible or not self._dirty or self._retry_blocked or self._reads.is_running():
            return
        remaining = self._merge_ms - (monotonic() - self._last_started) * 1000
        if remaining <= 0:
            self._timer.start(0)
        elif not self._timer.isActive():
            self._timer.start(max(1, int(remaining)))

    def _start(self) -> None:
        if self._closed or not self._visible or not self._dirty or self._retry_blocked or self._reads.is_running():
            return
        self._check_identity()
        identity, revision = self._identity, self._revision
        assert identity is not None
        self._dirty = False
        self._last_started = monotonic()

        def read():
            started = perf_counter()
            model = DashboardService(
                identity.user_database_path, static_database_path=identity.static_database_path,
            ).load()
            log_perf(logger, "home.summary_read", elapsed_ms=(perf_counter() - started) * 1000)
            return model

        def current() -> bool:
            return (not self._closed and self._visible and revision == self._revision
                    and identity == self._dependencies())

        def apply(model):
            if current():
                started = perf_counter()
                self._apply(model)
                log_perf(logger, "home.summary_apply", elapsed_ms=(perf_counter() - started) * 1000)

        def failed(error):
            if current():
                # 失败后等重新进页或新变动重试，不在定时器里空转重读。
                self._dirty = True
                self._retry_blocked = True
                self._failed(error)

        self._reads.submit(PageRequest((identity, revision), read, apply, failed))

    def retry(self) -> None:
        """重新进页允许重试上一暂态失败。"""
        self._last_started = 0.0
        self.refresh()

    def close(self) -> None:
        self._closed = True
        self._timer.stop()
        self._reads.close()


def initialize_dashboard(window) -> None:
    """组合根在导航与工作台控件就绪后显式接线。"""
    from src.features.home.page import refresh_home_page

    def loading():
        window.home_account_label.setText("正在读取当前账号工作台摘要…")
        for value, _subtitle in window.home_metric_labels.values():
            value.setText("—")
        window.home_last_sync_label.setText("正在读取已保存背包…")
        window.home_character_sync_detail.setText("正在读取已保存角色养成…")

    def failed(error):
        window.home_account_label.setText(f"工作台摘要读取失败：{error}；返回工作台可重试。")
        logger.warning(f"刷新工作台摘要失败: {error}")

    window.dashboard_controller = DashboardController(
        dependencies=lambda: DashboardDependencies(
            window.app_context.generation, window.app_context.account.user_database_path,
            window.app_context.paths.static_database_path,
        ), apply=lambda model: refresh_home_page(window, model), failed=failed,
        loading=loading, parent=window,
    )
    window.stack.currentChanged.connect(lambda index: window.dashboard_controller.set_visible(
        window._nav_key_for_index(index) == "home",
    ))
    window.dashboard_controller.set_visible(window._nav_key_for_index(window.stack.currentIndex()) == "home")
