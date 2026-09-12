# 验证长快照等待可取消、不会误停战报，并且失效 Core 不会再次复用。
from concurrent.futures import CancelledError
from queue import Queue
from threading import Event, Thread

import pytest

from src.integrations.nte_core_response_wait import wait_core_response
from src.services.inventory_capture_wait import InventorySyncCancelled
from src.services.native_game_session import NativeGameSession
from tests.test_native_inventory_sync_integration import ProjectionCore
from tests.test_nte_core_integration import fake_client


def test_core_call_cancellation_removes_pending_request_and_keeps_other_requests_usable():
    cancelled, waiting = Event(), Event()
    errors = []
    def check():
        waiting.set()
        if cancelled.is_set():
            raise CancelledError()
    with fake_client() as client:
        def read():
            try:
                client.call("test.timeout", timeout=65, check_cancelled=check)
            except Exception as error:
                errors.append(error)
        worker = Thread(target=read)
        worker.start()
        try:
            assert waiting.wait(1)
            cancelled.set()
            worker.join(1)
            assert not worker.is_alive()
            assert len(errors) == 1 and isinstance(errors[0], CancelledError)
            assert not client._pending
            assert client.status() == {"core_state": "idle"}
        finally:
            cancelled.set()
            worker.join(1)


@pytest.mark.parametrize("cancel_kind", ["lease", "close", "profile"])
def test_long_refresh_cancellation_aborts_idle_core_promptly(cancel_kind):
    entered, cancelled = Event(), Event()
    failures = []

    class SlowCore(ProjectionCore):
        def call(self, method, params, **kwargs):
            if method == "native.snapshot.refresh":
                assert kwargs["timeout"] == 65.0
                entered.set()
                return wait_core_response(Queue(), method=method, timeout=kwargs["timeout"],
                                          check_cancelled=kwargs["check_cancelled"])
            return super().call(method, params, **kwargs)

    core, replacement = SlowCore(count=1), ProjectionCore(count=1)
    core.hello_result["capabilities"].append("native_character_profile_v1")
    clients = iter((core, replacement))
    session = NativeGameSession(lambda: next(clients), lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")

    def profile_check():
        if cancelled.is_set():
            raise CancelledError("cancel profile")

    def read():
        try:
            if cancel_kind == "profile":
                session.read_character_profiles(check=profile_check)
            else:
                lease.status()
        except Exception as error:
            failures.append(error)

    worker = Thread(target=read)
    worker.start()
    try:
        assert entered.wait(1)
        if cancel_kind == "lease":
            lease.request_stop()
        elif cancel_kind == "close":
            session.request_close()
        else:
            cancelled.set()
        worker.join(1)
        assert not worker.is_alive(), "a cancelled 65-second refresh must return promptly"
        assert len(failures) == 1 and isinstance(failures[0], (InventorySyncCancelled, CancelledError))
        assert core.aborted and session._client is None
        lease.close()
        session.close()
        new_lease = session.inventory_client()
        assert session._client is replacement
        new_lease.close()
    finally:
        cancelled.set()
        lease.request_stop()
        session.request_close()
        worker.join(1)
        session.close()


def test_refresh_is_rejected_without_disturbing_active_battle():
    core = ProjectionCore(count=1)
    session = NativeGameSession(lambda: core, lambda _cap: None)
    battle = session.battle_client()
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    assert not lease.status()["native_snapshot_ready"]
    assert "native.snapshot.refresh" not in [method for method, _ in core.calls]
    lease.request_stop()
    lease.close()
    assert not core.aborted and not core.closed and session.battle_active
    assert battle.get_battle_record()["final"]
    battle.close()
    session.close()


def test_waiting_for_another_snapshot_lock_remains_cancellable():
    core = ProjectionCore(count=1)
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    lease.start_capture(profile="inventory")
    held, release = Event(), Event()
    def hold():
        with session._snapshot_lock:
            held.set()
            release.wait(2)
    holder = Thread(target=hold)
    holder.start()
    assert held.wait(1)
    failures, waiting = [], Event()
    def read():
        waiting.set()
        try:
            lease.status()
        except Exception as error:
            failures.append(error)
    reader = Thread(target=read)
    reader.start()
    assert waiting.wait(1)
    try:
        lease.request_stop()
        reader.join(1)
        assert not reader.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], InventorySyncCancelled)
        assert not core.aborted
    finally:
        release.set()
        holder.join(1)
        reader.join(1)
        lease.close()
        session.close()
