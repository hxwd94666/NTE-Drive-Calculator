# 在单一后台观察循环中编排游戏等待、同步与组件清理。
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from threading import Event, Lock, Thread
from typing import Callable


@dataclass(frozen=True)
class ObservationResult:
    phase: str
    detail: str = ""
    revision: int | None = None
    generation: int | None = None
    request_id: int = 0


class GameObservationService:
    """The owner lives only as long as Calc; explicit pause survives via policy."""

    def __init__(self, *, tick: Callable[[], object], publish: Callable[[object], None],
                 interval: float = 2.0) -> None:
        self._tick, self._publish, self._interval = tick, publish, interval
        self._stop = Event()
        self._wake = Event()
        self._finished = Event()
        self._lock = Lock()
        self._jobs: deque[tuple[str | None, Callable[[], object]]] = deque()
        self._finalize: Callable[[], None] | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._stop.is_set() or self._thread is not None:
                return
            self._start_locked()

    def _start_locked(self) -> None:
        self._thread = Thread(target=self._run, name="game-observer", daemon=True)
        self._thread.start()

    def submit(self, job: Callable[[], object], *, key: str | None = None) -> bool:
        """Serialize explicit checks and teardown on the existing observer owner."""
        with self._lock:
            if self._stop.is_set():
                return False
            if key is not None:
                self._jobs = deque((old_key, old) for old_key, old in self._jobs if old_key != key)
            self._jobs.append((key, job))
            if self._thread is None:
                self._start_locked()
            self._wake.set()
            return True

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                with self._lock:
                    self._wake.clear()
                    job = self._jobs.popleft()[1] if self._jobs else self._tick
                try:
                    result = job()
                except Exception:
                    result = ObservationResult("fault", "检测失败，请重新检测组件和游戏路径。")
                with self._lock:
                    if not self._stop.is_set() and result is not None:
                        self._publish(result)
                    has_jobs = bool(self._jobs)
                if not has_jobs:
                    self._wake.wait(self._interval)
        finally:
            try:
                if self._finalize is not None:
                    self._finalize()
            finally:
                self._finished.set()

    def close(self, *, finalize: Callable[[], None] | None = None) -> None:
        """Stop scheduling immediately; finite teardown runs on this same worker."""
        with self._lock:
            if self._stop.is_set():
                return
            self._finalize = finalize
            self._stop.set()
            self._jobs.clear()
            self._wake.set()
            if self._thread is None:
                if finalize is None:
                    self._finished.set()
                else:
                    self._start_locked()

    def wait_closed(self, timeout: float = 3.0) -> bool:
        return self._finished.wait(timeout)
