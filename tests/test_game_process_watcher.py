# 验证游戏监控的启动、绑定、退出重查、取消和进程身份隔离。
from queue import Queue
from threading import Event, Lock

from src.integrations.game_process_watcher import GameProcessIdentity, GameProcessWatcher


class FakeProcess:
    def __init__(self, pid=42, creation_time=100):
        self.identity = GameProcessIdentity(pid, creation_time)
        self.exited = Event()
        self.closed = Event()
        self.waiting = Event()

    def wait_exited(self, timeout):
        if timeout:
            self.waiting.set()
        return self.exited.wait(timeout)

    def close(self):
        self.closed.set()


class FakeBackend:
    def __init__(self, *results):
        self.results = list(results)
        self.scans = 0
        self.lock = Lock()
        self.searched = Event()

    def find_game(self):
        with self.lock:
            self.scans += 1
            result = self.results.pop(0) if self.results else None
        self.searched.set()
        if isinstance(result, Exception):
            raise result
        return result


def next_kind(events, kind):
    for _ in range(10):
        event = events.get(timeout=1)
        if event.kind == kind:
            return event
    raise AssertionError(f"No {kind} event")


def test_existing_game_binds_without_repeated_enumeration_and_duplicate_start():
    process, events = FakeProcess(), Queue()
    backend = FakeBackend(process)
    watcher = GameProcessWatcher(events.put, backend=backend, poll_interval=0.01)
    try:
        watcher.start()
        started = next_kind(events, "started")
        assert started.process == process.identity
        assert started.generation == watcher.generation
        assert process.waiting.wait(1)
        watcher.start()
        assert backend.scans == 1
        assert watcher.generation == started.generation
    finally:
        watcher.close()
    assert process.closed.wait(1)
    assert not watcher.is_running


def test_exit_resumes_discovery_and_distinguishes_reused_pid():
    first, second, events = FakeProcess(), FakeProcess(42, 200), Queue()
    watcher = GameProcessWatcher(events.put, backend=FakeBackend(first, second))
    try:
        watcher.start()
        assert next_kind(events, "started").process == first.identity
        first.exited.set()
        assert next_kind(events, "exited").process == first.identity
        assert next_kind(events, "waiting").process is None
        assert next_kind(events, "started").process == second.identity
        assert first.closed.wait(1)
    finally:
        watcher.close()
    assert second.closed.wait(1)


def test_waiting_for_game_is_cancelled_without_waiting_for_poll_interval():
    backend, events = FakeBackend(None), Queue()
    watcher = GameProcessWatcher(events.put, backend=backend, poll_interval=60)
    watcher.start()
    assert backend.searched.wait(1)
    watcher.stop()
    assert not watcher.is_running
    assert backend.scans == 1
    watcher.close()


def test_stop_suppresses_late_discovery_and_closes_handle():
    entered, release, process, events = Event(), Event(), FakeProcess(), Queue()

    class SlowBackend:
        def find_game(self):
            entered.set()
            assert release.wait(1)
            return process

    watcher = GameProcessWatcher(events.put, backend=SlowBackend())
    watcher.start()
    assert entered.wait(1)
    watcher.stop()
    assert watcher.worker_alive
    assert not watcher.is_running
    release.set()
    assert process.closed.wait(1)
    assert [event.kind for event in list(events.queue)] == ["waiting"]
    watcher.close()


def test_worker_remains_alive_until_bound_handle_cleanup_finishes():
    cleanup_entered, cleanup_release, events = Event(), Event(), Queue()

    class SlowCloseProcess(FakeProcess):
        def close(self):
            cleanup_entered.set()
            assert cleanup_release.wait(1)
            super().close()

    process = SlowCloseProcess()
    watcher = GameProcessWatcher(events.put, backend=FakeBackend(process))
    try:
        watcher.start()
        next_kind(events, "started")
        watcher.stop()
        assert cleanup_entered.wait(1)
        assert watcher.worker_alive
        assert not watcher.is_running
    finally:
        cleanup_release.set()
        watcher.close()
    assert process.closed.wait(1)


def test_bound_process_wait_failure_reports_identity_without_claiming_exit():
    events = Queue()

    class BrokenWaitProcess(FakeProcess):
        def wait_exited(self, timeout):
            if timeout:
                raise OSError("wait failed with private details")
            return False

    process = BrokenWaitProcess()
    watcher = GameProcessWatcher(events.put, backend=FakeBackend(process), poll_interval=0.01)
    try:
        watcher.start()
        next_kind(events, "started")
        failure = next_kind(events, "error")
        assert failure.process == process.identity
        assert "private" not in failure.error
        assert process.closed.wait(1)
        assert not any(event.kind == "exited" for event in list(events.queue))
    finally:
        watcher.close()


def test_restart_after_stop_has_new_generation_and_close_prevents_restart():
    first, second, events = FakeProcess(), FakeProcess(99, 300), Queue()
    watcher = GameProcessWatcher(events.put, backend=FakeBackend(first, second))
    watcher.start()
    previous = next_kind(events, "started")
    watcher.stop()
    watcher.start()
    current = next_kind(events, "started")
    assert current.generation != previous.generation
    assert current.generation == watcher.generation
    assert current.process == second.identity
    assert first.closed.wait(1)
    watcher.close()
    assert second.closed.wait(1)
    watcher.start()
    assert not watcher.is_running


def test_discovery_failure_reports_safe_error_then_recovers():
    events, process = Queue(), FakeProcess()
    watcher = GameProcessWatcher(
        events.put, backend=FakeBackend(OSError("private path"), process), poll_interval=0.01,
    )
    try:
        watcher.start()
        failure = next_kind(events, "error")
        assert "private path" not in failure.error
        assert next_kind(events, "started").process == process.identity
    finally:
        watcher.close()
    assert process.closed.wait(1)


def test_already_exited_discovery_is_not_announced_as_running():
    stale, current, events = FakeProcess(), FakeProcess(42, 200), Queue()
    stale.exited.set()
    watcher = GameProcessWatcher(events.put, backend=FakeBackend(stale, current))
    try:
        watcher.start()
        assert next_kind(events, "started").process == current.identity
        assert stale.closed.wait(1)
    finally:
        watcher.close()
    assert current.closed.wait(1)
