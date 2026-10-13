# 验证观察owner关闭有界、拒绝新任务且不发布迟到结果。
import threading
import time

from src.services.work_modes.observation import GameObservationService


def test_observer_close_is_nonblocking_and_never_publishes_late_tick():
    entered, release = threading.Event(), threading.Event()
    published, finalized = [], []

    def tick():
        entered.set()
        release.wait(2)
        return "late"

    observer = GameObservationService(tick=tick, publish=published.append, interval=0.001)
    observer.start()
    assert entered.wait(1)
    started = time.monotonic()
    observer.close(finalize=lambda: finalized.append("done"))
    assert time.monotonic() - started < 0.2
    release.set()
    assert observer.wait_closed(1)
    observer.start()
    assert not observer.submit(lambda: published.append("new-job"))
    assert published == [] and finalized == ["done"]


def test_observer_closed_before_start_cannot_create_new_work():
    called = []
    observer = GameObservationService(tick=lambda: called.append("tick"), publish=called.append)
    observer.close()
    observer.start()
    assert not observer.submit(lambda: called.append("job"))
    assert observer.wait_closed(0.1)
    assert called == []
