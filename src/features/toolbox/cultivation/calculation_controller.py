# 合并单与多角色养成的串行读取、准备缓存及关闭后迟到结果隔离。
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot
from src.app.page_tasks import PageRequest, PageTaskLane


@dataclass(frozen=True, slots=True)
class CalculationSubmission:
    revision: int
    request: Any
    identity: object
    kind: str


class CultivationCalculationController(QObject):
    """两个真实业务共用生命周期；不拥有历史写入与养成公式。"""
    result_ready = Signal(object)
    error = Signal(str)
    busy_changed = Signal(bool)
    preparation_ready = Signal(object)
    preparation_error = Signal(str)
    preparing_changed = Signal(bool)

    def __init__(self, service: Any, *, context_identity: Callable[[], object] | None,
                 parent: QObject) -> None:
        super().__init__(parent)
        self._service = service
        self._context_identity = context_identity
        self._lane = PageTaskLane(self)
        self._lane.idle.connect(self._on_idle)
        self._active: CalculationSubmission | None = None
        self._pending: CalculationSubmission | None = None
        self._preparation: tuple[object, Any] | None = None
        self._revision = 0
        self._closed = False

    def is_running(self) -> bool:
        # 交付信号不是线程结束；保留在父对象树内供应用退出检查。
        return self._lane.is_running()

    def submit(self, request: Any, identity: object) -> None:
        self._submit(request, identity, "calculate")

    def prepare(self, request: Any, identity: object) -> None:
        self._submit(request, identity, "prepare")

    def _submit(self, request: Any, identity: object, kind: str) -> None:
        if self._closed:
            return
        self._revision += 1
        submission = CalculationSubmission(self._revision, request, identity, kind)
        if self.is_running():
            self._trace(submission, "controller.queued")
            self._pending = submission
        else:
            self._start(submission)

    def invalidate(self, *, clear_preparation: bool = False) -> None:
        if self._closed:
            return
        self._revision += 1
        self._pending = None
        self._lane.cancel()
        if clear_preparation:
            self._preparation = None

    def close(self) -> None:
        self._trace(self._active, "controller.close")
        self._closed = True
        self._revision += 1
        self._pending = None
        self._preparation = None
        # 立即撤销交付，不在 GUI wait；页面须等物理终态再销毁。
        self._lane.close()

    def _start(self, submission: CalculationSubmission) -> None:
        if not self._context_matches(submission.identity):
            self.busy_changed.emit(False)
            self.preparing_changed.emit(False)
            return
        self._active = submission
        read = self._make_read(submission.request, submission.identity, submission.kind)
        self._trace(submission, "controller.worker_started")
        self.busy_changed.emit(submission.kind == "calculate")
        self.preparing_changed.emit(submission.kind == "prepare")
        self._lane.submit(PageRequest(submission.revision, read, self._on_result, self._on_error))

    def _make_read(self, request: Any, identity: object, kind: str) -> Callable[[], Any]:
        raise NotImplementedError

    def _result_preparation(self, result: Any, kind: str) -> Any:
        raise NotImplementedError

    def _trace(self, submission: CalculationSubmission | None, event: str, **fields: Any) -> None:
        pass

    def _is_current(self) -> bool:
        active = self._active
        return (not self._closed and active is not None and active.revision == self._revision
                and self._context_matches(active.identity))

    def _context_matches(self, identity: object) -> bool:
        try:
            return self._context_identity is None or self._context_identity() == identity
        except (OSError, RuntimeError):
            return False

    @Slot(object)
    def _on_result(self, result: Any) -> None:
        active = self._active
        if not self._is_current():
            self._trace(active, "controller.result_discarded")
            return
        self._trace(active, "controller.result_accepted")
        prepared = self._result_preparation(result, active.kind)
        if prepared is not None:
            self._preparation = (active.identity, prepared)
        signal = self.preparation_ready if active.kind == "prepare" else self.result_ready
        signal.emit(result)

    @Slot(str)
    def _on_error(self, message: str) -> None:
        if self._is_current():
            self._trace(self._active, "controller.error_accepted")
            signal = self.preparation_error if self._active.kind == "prepare" else self.error
            signal.emit(message)

    @Slot()
    def _on_idle(self) -> None:
        self._trace(self._active, "controller.worker_finished", gui_thread=True)
        self._active = None
        pending, self._pending = self._pending, None
        if self._closed:
            return
        if pending is not None:
            self._start(pending)
        else:
            self.busy_changed.emit(False)
            self.preparing_changed.emit(False)
