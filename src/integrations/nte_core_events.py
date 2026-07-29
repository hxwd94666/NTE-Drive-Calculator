# 合并高频战斗摘要并保持其他 nte-core 事件的可靠顺序。
"""Coalescing queue used by the nte-core callback dispatcher."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque


def _queued_event_method(item: object) -> str | None:
    event = item[0] if isinstance(item, tuple) else item
    if not isinstance(event, dict):
        return None
    method = event.get("method")
    return method if isinstance(method, str) else None


class CoalescingEventQueue:
    """Keep reliable event order while retaining only the latest battle summary."""

    def __init__(self) -> None:
        self._items: deque[object] = deque()
        self._condition = threading.Condition()

    def put(self, item: object) -> None:
        with self._condition:
            if _queued_event_method(item) == "event.battle.summary":
                for index in range(len(self._items) - 1, -1, -1):
                    if (
                        _queued_event_method(self._items[index])
                        == "event.battle.summary"
                    ):
                        del self._items[index]
                        break
            self._items.append(item)
            self._condition.notify()

    def get(self, timeout: float | None = None) -> object:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be a non-negative number")
        with self._condition:
            if timeout is None:
                while not self._items:
                    self._condition.wait()
            else:
                deadline = time.monotonic() + timeout
                while not self._items:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    self._condition.wait(remaining)
            return self._items.popleft()

    def get_nowait(self) -> object:
        return self.get(timeout=0.0)

