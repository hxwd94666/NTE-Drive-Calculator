# 管理多角色养成计算的单工作器、末次请求与上下文过期丢弃。
"""Asynchronous owner for batch cultivation calculations."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from src.app.workers import WorkerThread
from src.services.cultivation_batch_planner_service import (
    CultivationBatchPlannerService,
    CultivationBatchRequest,
)
from src.utils.cultivation_trace import trace_cultivation


_DRAINING_WORKERS: set[WorkerThread] = set()


class CultivationBatchController(QObject):
    """Serialize batch solvers and only publish the newest current-context result."""

    result_ready = Signal(object)
    error = Signal(str)
    busy_changed = Signal(bool)

    def __init__(
        self,
        service: CultivationBatchPlannerService,
        *,
        context_identity: Callable[[], object] | None,
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._context_identity = context_identity
        self._worker: WorkerThread | None = None
        self._active: tuple[int, object, int] | None = None
        self._pending: tuple[int, CultivationBatchRequest, object] | None = None
        self._revision = 0
        self._closed = False

    def submit(self, request: CultivationBatchRequest, identity: object) -> None:
        if self._closed:
            return
        self._revision += 1
        submission = (self._revision, request, identity)
        # Keep the old worker owned until its queued finished slot has run.
        if self._worker is not None:
            trace_cultivation(request.trace_id, "controller.queued")
            self._pending = submission
            return
        self._start(submission)

    def invalidate(self) -> None:
        """Revoke a running or queued result as soon as the draft changes."""

        if self._closed:
            return
        self._revision += 1
        self._pending = None

    def close(self) -> None:
        trace_cultivation(self._active[2] if self._active else 0, "controller.close")
        self._closed = True
        self._revision += 1
        self._pending = None
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            if not worker.wait(5_000):
                # A still-running QThread must outlive the page that owned it.
                worker.setParent(None)
                _DRAINING_WORKERS.add(worker)
                worker.finished.connect(
                    lambda running=worker: _DRAINING_WORKERS.discard(running)
                )
                self._worker = None
                self._active = None

    def _start(
        self,
        submission: tuple[int, CultivationBatchRequest, object],
    ) -> None:
        revision, request, identity = submission
        worker = WorkerThread(
            target=lambda: self._service.calculate(request),
            parent=self,
        )
        self._worker = worker
        self._active = (revision, identity, request.trace_id)
        trace_cultivation(request.trace_id, "controller.worker_started")
        worker.result_ready.connect(self._on_result, Qt.ConnectionType.QueuedConnection)
        worker.error.connect(self._on_error, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        self.busy_changed.emit(True)
        worker.start()

    @Slot(object)
    def _on_result(self, result: object) -> None:
        active = self._active
        if active is None:
            return
        revision, identity, trace_id = active
        gui_thread = QThread.currentThread() == self.thread()
        trace_cultivation(trace_id, "controller.result_slot", gui_thread=gui_thread)
        if not gui_thread:
            return
        if self._is_current(revision, identity):
            trace_cultivation(trace_id, "controller.result_accepted")
            self.result_ready.emit(result)
        else:
            trace_cultivation(trace_id, "controller.result_discarded")

    @Slot(str)
    def _on_error(self, message: str) -> None:
        active = self._active
        if active is None:
            return
        revision, identity, trace_id = active
        gui_thread = QThread.currentThread() == self.thread()
        trace_cultivation(trace_id, "controller.error_slot", gui_thread=gui_thread)
        if not gui_thread:
            return
        if self._is_current(revision, identity):
            trace_cultivation(trace_id, "controller.error_accepted")
            self.error.emit(message)

    @Slot()
    def _on_finished(self) -> None:
        active = self._active
        trace_id = active[2] if active else 0
        gui_thread = QThread.currentThread() == self.thread()
        trace_cultivation(trace_id, "controller.worker_finished", gui_thread=gui_thread)
        if not gui_thread:
            return
        self._worker = None
        self._active = None
        if self._closed:
            return
        pending = self._pending
        self._pending = None
        if pending is not None:
            self._start(pending)
        else:
            self.busy_changed.emit(False)

    def _is_current(self, revision: int, identity: object) -> bool:
        if self._closed or revision != self._revision:
            return False
        if self._context_identity is None:
            return True
        return self._context_identity() == identity


__all__ = ["CultivationBatchController"]
