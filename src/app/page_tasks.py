# 管理页面后台任务的有界合并、主线程回调和取消生命周期。
"""One running task and one latest request per page owner; no UI-thread waits."""

from __future__ import annotations

from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot


@dataclass(frozen=True)
class PageRequest:
    key: Hashable
    read: Callable[[], Any]
    apply: Callable[[Any], None]
    failed: Callable[[str], None]
    discarded: Callable[[], None] = lambda: None


class _PageWorker(QThread):
    delivered = Signal(object)
    failed = Signal(str)

    def __init__(self, read: Callable[[], Any], parent: QObject) -> None:
        super().__init__(parent)
        self._read = read

    def run(self) -> None:
        try:
            self.delivered.emit(self._read())
        except (Exception, SystemExit) as exc:
            self.failed.emit(str(exc))


class PageTaskLane(QObject):
    """Own reads until finished, apply only the newest request on the UI thread."""

    idle = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker: _PageWorker | None = None
        self._active: PageRequest | None = None
        self._latest: PageRequest | None = None
        self._closed = False

    def is_running(self) -> bool:
        return self._worker is not None

    def submit(self, request: PageRequest) -> None:
        if self._closed:
            return
        if self._latest is not None and self._latest.key != request.key:
            self._latest.discarded()
        self._latest = request
        if self._worker is None:
            self._start()

    def _start(self) -> None:
        request = self._latest
        if request is None or self._closed:
            return
        self._active = request
        worker = _PageWorker(request.read, self)
        self._worker = worker
        worker.delivered.connect(self._deliver)
        worker.failed.connect(self._fail)
        worker.finished.connect(self._finished)
        worker.start()

    @Slot(object)
    def _deliver(self, value: Any) -> None:
        latest = self._latest
        if not self._closed and latest is not None and self._active is not None and latest.key == self._active.key:
            # The newest same-key caller gets the result; repeated clicks don't spawn threads.
            self._latest = None
            try:
                latest.apply(value)
            except Exception as exc:
                latest.failed(str(exc))

    @Slot(str)
    def _fail(self, error: str) -> None:
        latest = self._latest
        if not self._closed and latest is not None and self._active is not None and latest.key == self._active.key:
            self._latest = None
            latest.failed(error)

    @Slot()
    def _finished(self) -> None:
        worker = self._worker
        self._worker = None
        self._active = None
        if worker is not None:
            worker.deleteLater()
        if self._latest is not None and not self._closed:
            self._start()
        else:
            self.idle.emit()

    def cancel(self) -> None:
        """Invalidate delivery, not a SQLite commit or an in-flight OS call."""
        if self._latest is not None:
            self._latest.discarded()
        self._latest = None

    def close(self) -> None:
        self._closed = True
        self.cancel()


class PageCommitLane(QObject):
    """A single commit, never coalesced/cancelled/replayed; acknowledge at terminal state."""

    settled = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker: _PageWorker | None = None
        self._callbacks = None
        self._value = None
        self._error: str | None = None
        self._delivered = False

    def is_running(self) -> bool:
        return self._worker is not None

    def submit(self, commit, committed, failed, application_failed) -> bool:
        if self.is_running():
            return False
        self._value, self._error = None, None
        self._delivered = False
        self._callbacks = committed, failed, application_failed
        worker = _PageWorker(commit, self)
        self._worker = worker
        worker.delivered.connect(self._store_result)
        worker.failed.connect(self._store_error)
        worker.finished.connect(self._finished)
        worker.start()
        return True

    @Slot(object)
    def _store_result(self, value) -> None:
        self._delivered = True
        self._value = value

    @Slot(str)
    def _store_error(self, error) -> None:
        self._error = error

    @Slot()
    def _finished(self) -> None:
        worker, callbacks = self._worker, self._callbacks
        value, error = self._value, self._error
        self._worker = self._callbacks = self._value = None
        if worker is not None:
            worker.deleteLater()
        success = self._delivered and error is None
        if not success and error is None:
            error = "提交线程结束但未返回确认；请核对已保存内容。"
        if callbacks is not None:
            committed, failed, application_failed = callbacks
            try:
                if success:
                    committed(value)
                else:
                    failed(error)
            except Exception as exc:
                # This is a UI acknowledgement failure, not a transaction failure.
                application_failed(str(exc))
                success = False
        self.settled.emit(success)


def close_page_tasks(owner: QObject) -> bool:
    """Invalidate reads; caller defers owner destruction while threads finish."""
    lanes = owner.findChildren(PageTaskLane)
    for lane in lanes:
        lane.close()
    return any(lane.is_running() for lane in lanes) or any(
        lane.is_running() for lane in owner.findChildren(PageCommitLane)
    )


def delete_when_page_tasks_finish(owner: QObject) -> None:
    """撤销读取，等物理线程与已接受提交结束才销毁，不脱离父对象。"""
    if close_page_tasks(owner):
        timer = QTimer(owner)
        timer.setInterval(25)

        def finish():
            if not close_page_tasks(owner):
                timer.stop()
                owner.deleteLater()

        timer.timeout.connect(finish)
        timer.start()
    else:
        owner.deleteLater()


def start_ui_latency_monitor(owner: QObject, page_provider) -> QTimer:
    """Low-frequency event-loop lag sampling; no per-tick logs or user payloads."""
    from time import perf_counter
    from src.utils.logger import logger
    from src.utils.perf import log_perf
    last_tick, last_log = perf_counter(), 0.0
    timer = QTimer(owner)

    def tick():
        nonlocal last_tick, last_log
        now = perf_counter()
        lag = max(0.0, (now - last_tick) * 1000 - 250)
        last_tick = now
        if lag >= 250 and now - last_log >= 5:
            last_log = now
            log_perf(logger, "ui.event_loop_lag", elapsed_ms=lag, page=page_provider())

    timer.timeout.connect(tick)
    timer.start(250)
    return timer
