# 在保留每次 RPC 超时的同时轮询取消标记，避免长快照请求阻塞停止。
from __future__ import annotations

import queue
from time import monotonic

from src.integrations.nte_core_protocol import NteCoreTimeoutError


def wait_core_response(response_queue, *, method: str, timeout: float, check_cancelled=None):
    deadline = monotonic() + timeout
    while True:
        if check_cancelled is not None:
            check_cancelled()
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise NteCoreTimeoutError(method, timeout)
        try:
            result = response_queue.get(timeout=min(remaining, 0.1) if check_cancelled is not None else remaining)
        except queue.Empty:
            continue
        if check_cancelled is not None:
            check_cancelled()
        return result
