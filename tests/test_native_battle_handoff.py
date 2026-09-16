# 验证手动战报优先取消自动刷新并等待原所有者收尾，且不持久暂停同步。
from concurrent.futures import CancelledError
from queue import Queue
from threading import Event, Thread

import pytest

from src.integrations.nte_core_response_wait import wait_core_response
from src.services.inventory_capture_wait import InventorySyncCancelled
from src.services.native_game_session import NativeGameSession
from tests.test_native_inventory_sync_integration import ProjectionCore


@pytest.mark.parametrize("source", ["inspection", "inventory", "character"])
def test_battle_cancels_refresh_waits_for_abort_then_allows_sync_again(source):
    entered, aborting, finish_abort, battle_done = Event(), Event(), Event(), Event()
    failures, battles, created = [], [], []

    class SlowCore(ProjectionCore):
        def call(self, method, params, **kwargs):
            if method == "native.snapshot.refresh":
                entered.set()
                return wait_core_response(Queue(), method=method, timeout=65,
                                          check_cancelled=kwargs["check_cancelled"])
            return super().call(method, params, **kwargs)

        def abort(self):
            aborting.set()
            assert finish_abort.wait(2)
            super().abort()

    old, new = SlowCore(count=1), ProjectionCore(count=1)
    old.hello_result["capabilities"].append("native_character_profile_v1")

    def factory():
        if created:
            assert old.aborted, "the old Core must finish aborting before replacement starts"
        client = old if not created else new
        created.append(client)
        return client

    session = NativeGameSession(factory, lambda _cap: None)
    inventory = session.inventory_client() if source == "inventory" else None
    if inventory:
        inventory.start_capture(profile="inventory")

    def refresh():
        try:
            if inventory:
                inventory.status()
            elif source == "character":
                session.read_character_profiles()
            else:
                session.inspect(refresh=True)
        except Exception as error:
            failures.append(error)
        finally:
            if inventory:
                inventory.close()

    def start_battle():
        try:
            battles.append(session.battle_client())
        finally:
            battle_done.set()

    reader, starter = Thread(target=refresh), Thread(target=start_battle)
    reader.start()
    try:
        assert entered.wait(1)
        starter.start()
        assert aborting.wait(1)
        assert not battle_done.is_set() and not session.battle_active
        assert created == [old]
        with pytest.raises(InventorySyncCancelled):
            session.inspect(refresh=True)
        with pytest.raises(RuntimeError, match="已有原生战报"):
            session.battle_client()
        finish_abort.set()
        reader.join(1)
        starter.join(1)
        assert not reader.is_alive() and not starter.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], (InventorySyncCancelled, CancelledError))
        assert session.battle_active and created == [old, new]
        assert not new.closed and not new.aborted
        battle = battles[0]
        battle.start_capture(profile="combat")
        with pytest.raises(RuntimeError, match="已有原生战报"):
            session.battle_client()
        assert battle.get_battle_record()["final"] and not new.aborted
        battle.close()
        resumed = session.inventory_client()
        resumed.start_capture(profile="inventory")
        assert resumed.status()["native_snapshot_ready"]
        resumed.close()
    finally:
        finish_abort.set()
        session.request_close()
        reader.join(2)
        if starter.ident is not None:
            starter.join(2)
        for battle in battles:
            battle.close()
        session.close()


@pytest.mark.parametrize("revocation", ["close", "context", "permission", "timeout", "manual_stop"])
def test_battle_handoff_wait_is_bounded_and_revocable(monkeypatch, revocation):
    held, release, pending, stopped = Event(), Event(), Event(), Event()
    context, allowed = [1], [True]
    failures, clients = [], []

    def guard(_cap):
        if not allowed[0]:
            raise PermissionError("revoked")

    session = NativeGameSession(lambda: clients.append(ProjectionCore()) or clients[-1],
                                guard, lambda: context[0])
    if revocation == "timeout":
        monkeypatch.setattr("src.services.native_game_session.NATIVE_BATTLE_HANDOFF_TIMEOUT_SECONDS", 0.05)

    def hold():
        with session._snapshot_lock:
            held.set()
            assert release.wait(2)

    def start():
        def check():
            pending.set()
            if stopped.is_set():
                raise CancelledError("manual stop")
        try:
            session.battle_client(check=check)
        except Exception as error:
            failures.append(error)

    holder, starter = Thread(target=hold), Thread(target=start)
    holder.start()
    assert held.wait(1)
    starter.start()
    assert pending.wait(1)
    try:
        if revocation == "close":
            session.request_close()
        elif revocation == "context":
            context[0] = 2
        elif revocation == "permission":
            allowed[0] = False
        elif revocation == "manual_stop":
            stopped.set()
        starter.join(1)
        assert not starter.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], (RuntimeError, PermissionError, CancelledError))
        assert clients == [] and not session.battle_active
        assert not session._battle_requested.is_set()
    finally:
        release.set()
        holder.join(1)
        starter.join(1)
        session.close()
