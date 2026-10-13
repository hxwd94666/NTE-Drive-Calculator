# 验证加权owner的账号代次、不可复活token、精确任务交付和物理退场。
from concurrent.futures import CancelledError
from dataclasses import replace
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from src.app.context import CallbackAccountLifecycle
from src.app.workers import WorkerThread
from src.features.weighted_allocation.save_owner import WeightedOwnerIdentity, WeightedSaveOwner
from tests.test_weighted_allocation_boundaries import make_context

NTE_TEST_TIER = "core"


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def pump(app, condition, *, timeout=4):
    deadline = monotonic() + timeout
    while not condition():
        assert monotonic() < deadline, "Qt物理退场超过有界截止时间"
        app.processEvents()
        Event().wait(.001)
    app.processEvents()


def owned_preview(owner, identity):
    source = owner.capture(identity)
    return SimpleNamespace(account_id=identity.account_id, user_database_path=identity.user_database_path,
                           ownership=source, result=SimpleNamespace(unified=SimpleNamespace(selected=(object(),))))


@pytest.mark.parametrize("changed", ["account", "generation", "path"])
def test_independent_owner_identity_rejects_late_save(tmp_path, changed):
    current = [WeightedOwnerIdentity("a", 7, (tmp_path / "a.sqlite3").resolve())]
    owner = WeightedSaveOwner(lambda: current[0])
    preview = owned_preview(owner, current[0])
    task = owner.begin_save(preview)
    current[0] = replace(current[0], **{
        "account": {"account_id": "b"}, "generation": {"generation": 9},
        "path": {"user_database_path": (tmp_path / "b.sqlite3").resolve()},
    }[changed])
    with pytest.raises(CancelledError):
        task.checkpoint()


def test_same_path_aba_and_rebuild_never_revive_old_event(tmp_path):
    current = [WeightedOwnerIdentity("a", 7, (tmp_path / "a.sqlite3").resolve())]
    owner = WeightedSaveOwner(lambda: current[0])
    preview = owned_preview(owner, current[0])
    assert owner.request_stop()
    current[0] = replace(current[0], generation=9)
    owner.rebuild()
    newer = owned_preview(owner, current[0])
    assert owner.accepts(newer.ownership)
    with pytest.raises(CancelledError):
        owner.begin_save(preview)
    current[0] = replace(current[0], generation=7)
    with pytest.raises(CancelledError):
        preview.ownership.checkpoint()


def test_management_admission_remains_closed_across_context_rebuild(tmp_path):
    current = [WeightedOwnerIdentity("a", 7, (tmp_path / "a.sqlite3").resolve())]
    owner = WeightedSaveOwner(lambda: current[0])
    with owner.management_hold():
        current[0] = replace(current[0], account_id="b", generation=8)
        owner.rebuild()
        assert not owner.can_submit()
        with pytest.raises(CancelledError):
            owner.capture(current[0])
    assert owner.can_submit()
    assert owner.capture(current[0]).identity.generation == 8


def test_revoked_delivery_is_suppressed_before_new_task_admission(qt_app, tmp_path):
    identity = WeightedOwnerIdentity("a", 7, (tmp_path / "a.sqlite3").resolve())
    owner = WeightedSaveOwner(lambda: identity)
    old = owned_preview(owner, identity)
    old_task = owner.begin_save(old)
    release, entered = Event(), Event()
    old_callbacks = Mock()
    def work():
        entered.set()
        assert release.wait(3)
        return old
    owner.submit(old_task, work, old_callbacks, old_callbacks, application_failed=old_callbacks)
    try:
        pump(qt_app, entered.is_set)
        assert not owner.request_stop()
        with pytest.raises(CancelledError):
            owner.capture(identity)
    finally:
        release.set()
        pump(qt_app, owner.try_retire)
    old_callbacks.assert_not_called()
    owner.rebuild()
    newer = owned_preview(owner, identity)
    newer_task = owner.begin_save(newer)
    delivered = []
    owner.submit(newer_task, lambda: newer, delivered.append, old_callbacks, application_failed=old_callbacks)
    assert owner.current_task is newer_task
    pump(qt_app, owner.try_retire)
    assert delivered == [newer] and owner.current_task is None
    old_callbacks.assert_not_called()


def test_result_ready_is_not_physical_finish_and_context_stop_fails_closed(qt_app, tmp_path):
    context = make_context(tmp_path)
    identity = WeightedOwnerIdentity(context.account.active_account_id, context.generation,
                                     Path(context.account.user_database_path).resolve())
    owner = WeightedSaveOwner(lambda: WeightedOwnerIdentity(context.account.active_account_id,
                            context.generation, Path(context.account.user_database_path).resolve()))
    context.register_account_lifecycle(CallbackAccountLifecycle(
        is_running=owner.is_running, stop=owner.stop, rebuild=lambda _a: owner.rebuild(), start=lambda: None,
    ))
    result_ready, release = Event(), Event()
    class FinishingWorker(WorkerThread):
        def run(self):
            super().run()
            result_ready.set()
            assert release.wait(3), "测试物理退场屏障未释放"
    worker = FinishingWorker(target=lambda: "ready")
    result = []
    worker.result_ready.connect(result.append)
    owner.track(worker)
    worker.start()
    try:
        pump(qt_app, lambda: result_ready.is_set() and result == ["ready"])
        assert owner.is_running()
        before = context.generation
        new_account = replace(context.account, active_account_id="b", user_database_path=tmp_path / "b.sqlite3")
        with pytest.raises(RuntimeError, match="物理退场"):
            context.switch_account(new_account)
        assert context.generation == before
        assert context.account.active_account_id == identity.account_id
        continuations = []
        owner.when_idle(lambda: continuations.append("idle"))
        qt_app.processEvents()
        assert continuations == []
    finally:
        release.set()
        pump(qt_app, owner.try_retire)
        worker.wait(500)
    assert continuations == ["idle"]


@pytest.mark.parametrize("kind", ["calculation", "save"])
@pytest.mark.parametrize("transition", ["account", "timeout"])
def test_owner_idle_reopens_admission_without_delivering_revoked_work(qt_app, tmp_path, kind, transition):
    current = [WeightedOwnerIdentity("a", 7, (tmp_path / "a.sqlite3").resolve())]
    owner = WeightedSaveOwner(lambda: current[0])
    release, entered = Event(), Event()
    def target():
        entered.set()
        assert release.wait(3)
        return "done"
    old_callbacks = Mock()
    if kind == "save":
        preview = owned_preview(owner, current[0])
        task = owner.begin_save(preview)
        owner.submit(task, target, old_callbacks, old_callbacks, application_failed=old_callbacks)
    else:
        handle = owner.capture(current[0])
        worker = WorkerThread(target=target, parent=owner)
        worker.result_ready.connect(lambda value: old_callbacks(value) if owner.accepts(handle) else None)
        owner.track(worker)
        worker.start()
    try:
        pump(qt_app, entered.is_set)
        assert not owner.request_stop()
        continuations, expired = [], []
        def change_account():
            continuations.append("once")
            current[0] = replace(current[0], account_id="b", generation=8)
            owner.rebuild()
        owner.when_idle(change_account, timeout_ms=20 if transition == "timeout" else 3000,
                        timed_out=lambda: expired.append("expired"))
        if transition == "timeout":
            pump(qt_app, lambda: expired == ["expired"])
            assert current[0].account_id == "a" and continuations == []
        qt_app.processEvents()
        assert not owner.can_submit()
    finally:
        release.set()
        pump(qt_app, owner.try_retire)
    assert owner.can_submit()
    old_callbacks.assert_not_called()
    assert current[0].account_id == ("b" if transition == "account" else "a")
    assert continuations == (["once"] if transition == "account" else [])








