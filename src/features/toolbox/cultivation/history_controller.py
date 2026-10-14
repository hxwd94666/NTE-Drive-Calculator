# 串行执行账号养成历史读写并隔离过期回调与敏感错误日志。
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from functools import partial
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from src.app.page_tasks import PageCommitLane
from src.domain.progression.history import HistoryConflict, HistoryContextExpired
from src.services.progression.history_service import CultivationHistoryService
from src.storage.sqlite.user_data_support import UserDataError
from src.utils.cultivation_trace import trace_cultivation

_OPERATIONS = frozenset({"save", "list", "get", "select_all", "delete", "restore"})


@dataclass(frozen=True, slots=True)
class HistoryOperationResult:
    operation: str
    request_id: int
    value: object = None
    error_code: str | None = None
    message: str = ""


def execute_history_operation(
    operation: str, request_id: int, action: Callable[[], object], cancelled: Event,
) -> HistoryOperationResult:
    started = perf_counter()
    trace_cultivation(request_id, f"history.{operation}.begin")
    try:
        if cancelled.is_set():
            raise HistoryContextExpired("养成历史上下文已失效")
        result = HistoryOperationResult(operation, request_id, value=action())
    except HistoryContextExpired:
        result = HistoryOperationResult(operation, request_id, error_code="expired")
    except HistoryConflict as error:
        result = HistoryOperationResult(operation, request_id, error_code="conflict", message=str(error))
    except ValueError as error:
        result = HistoryOperationResult(operation, request_id, error_code="invalid_data", message=str(error))
    except UserDataError:
        result = HistoryOperationResult(
            operation, request_id, error_code="storage_failed",
            message="历史数据库操作失败；请检查文件占用和可用空间后重试。",
        )
    except Exception:
        # 在普通执行器内归类异常，避免线程日志输出恢复 payload 或原始错误。
        result = HistoryOperationResult(
            operation, request_id, error_code="operation_failed", message="历史操作未完成，请重试。",
        )
    trace_cultivation(
        request_id, f"history.{operation}.end", failed=result.error_code is not None,
        elapsed_ms=int((perf_counter() - started) * 1000),
    )
    return result


class CultivationHistoryController(QObject):
    """串行历史动作；读取仅合并尚未启动的同类请求，写入从不重放。"""

    completed = Signal(object)

    def __init__(self, service: CultivationHistoryService, *,
                 context_identity: Callable[[], object], parent: QObject) -> None:
        super().__init__(parent)
        self._service = service
        self._identity = context_identity
        self._initial_identity = context_identity()
        self._lane = PageCommitLane(self)
        self._lane.settled.connect(self._on_finished)
        self._pending: deque[tuple[str, int, Callable[[], object]]] = deque()
        self._sequence = 0
        self._closed = False
        self._cancelled = Event()

    def is_running(self) -> bool:
        return self._lane.is_running()

    def submit(self, operation: str, action: Callable[[], object]) -> int:
        if operation not in _OPERATIONS:
            raise ValueError("养成历史操作无效")
        if self._closed:
            return 0
        self._sequence += 1
        request_id = self._sequence
        if operation in {"list", "get", "select_all"}:
            # 未启动的读取只留末次；保存与确切删除仍按原顺序，不合并。
            self._pending = deque(item for item in self._pending if item[0] != operation)
        self._pending.append((operation, request_id, action))
        self._start_next()
        return request_id

    def _context_matches(self) -> bool:
        # 页面身份 provider 只在 GUI 调用；后台 Service 另有自己的事务边界。
        try:
            return self._identity() == self._initial_identity
        except (OSError, RuntimeError):
            return False

    def _start_next(self) -> None:
        if self._closed or self.is_running() or not self._pending:
            return
        if not self._context_matches():
            self.close()
            return
        operation, request_id, action = self._pending.popleft()
        # 后台仅持普通动作、冻结标识与线程安全 Event，不捕获 Controller。
        read = partial(execute_history_operation, operation, request_id, action, self._cancelled)
        self._lane.submit(read, self._on_result,
                          partial(self._dispatch_failed, operation, request_id),
                          self._acknowledgement_failed)

    @Slot(object)
    def _on_result(self, result: object) -> None:
        if not self._closed and self._context_matches():
            self.completed.emit(result)

    def _dispatch_failed(self, operation: str, request_id: int, _message: str) -> None:
        self._on_result(HistoryOperationResult(
            operation, request_id, error_code="operation_failed", message="历史操作未完成，请重试。",
        ))

    def _acknowledgement_failed(self, _message: str) -> None:
        # 回调失败不代表事务失败，不能自动重发；不记录原始错误或 payload。
        trace_cultivation(0, "history.acknowledgement_failed", failed=True)

    @Slot(bool)
    def _on_finished(self, _acknowledged: bool) -> None:
        self._start_next()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending.clear()
        self._cancelled.set()
        self._service.close()
        # 撤销排队和提交授权前动作；授权后的冻结旧账号事务可完整提交或因错误回滚。
        # 不等待、不强杀、不重发，物理线程留在父对象树供退出/退场门禁等待。
