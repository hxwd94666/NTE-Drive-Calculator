# 验证原生通知合并、静默复核和变化期间禁止提交旧快照。
from copy import deepcopy
from threading import Event, Thread, current_thread

import pytest

from src.integrations.nte_core_protocol import NteCoreProtocolError
from src.services.native_snapshot_changes import NativeSnapshotChanges, snapshot_change_key
from src.services.native_game_session import NativeGameSession
from tests.test_native_inventory_sync_integration import ProjectionCore


def status(revision="1", *, dirty=False, provider="p"):
    return {"providerId": provider, "domains": [
        {"domain": d, "providerId": provider, "domainKey": "scene/" + d,
         "revision": revision, "dirty": dirty, "enabled": True, "ready": not dirty}
        for d in ("inventory", "character", "team", "environment")]}


def test_unchanged_status_does_not_repeat_full_reads_after_time_passes():
    now = [0.0]
    changes = NativeSnapshotChanges(clock=lambda: now[0])
    current = status()
    assert changes.needs_refresh(current, "inventory")
    changes.accept(current, "inventory")
    now[0] = 1
    assert not changes.needs_refresh(current, "inventory")
    now[0] = 31
    assert not changes.needs_refresh(current, "inventory")
    now[0] = 3600
    assert not changes.needs_refresh(current, "inventory")


def test_notification_burst_waits_for_stable_revision_and_keeps_domains_independent():
    now = [0.0]
    changes = NativeSnapshotChanges(clock=lambda: now[0])
    current = status()
    changes.accept(current, "inventory")
    now[0] = 1
    changed = status("2", dirty=True)
    assert not changes.needs_refresh(changed, "inventory")
    now[0] = 1.2
    assert not changes.needs_refresh(status("3", dirty=True), "inventory")
    now[0] = 2
    assert changes.needs_refresh(status("3", dirty=True), "inventory")
    assert changes.needs_refresh(current, "character")


def test_provider_reconnect_cannot_reuse_equal_revision():
    changes = NativeSnapshotChanges(clock=lambda: 0)
    changes.accept(status(), "inventory")
    assert not changes.is_current(status(provider="new"), "inventory")


@pytest.mark.parametrize("patch", [{"revision": True}, {"revision": "-1"},
                                   {"revision": "01"}, {"dirty": 1}])
def test_invalid_change_metadata_is_not_treated_as_quiet(patch):
    current = status()
    current["domains"][0].update(patch)
    with pytest.raises(NteCoreProtocolError):
        snapshot_change_key(current, "inventory")


def test_dirty_or_missing_domain_cannot_validate_saved_snapshot():
    changes = NativeSnapshotChanges(clock=lambda: 0)
    changes.accept(status(), "inventory")
    assert not changes.is_current(status(dirty=True), "inventory")
    invalid = deepcopy(status())
    invalid["domains"] = []
    with pytest.raises(NteCoreProtocolError):
        changes.is_current(invalid, "inventory")


class ChangeCore(ProjectionCore):
    def __init__(self):
        super().__init__(count=1)
        self.hello_result["capabilities"].append("snapshot.changes.v1")
        self.header.update(revision="1", dirty=False)
        self.dirty = False

    def call(self, method, params, **kwargs):
        if method == "native.snapshot.status":
            self.calls.append((method, params))
            return {"providerId": "fixture", "domains": [dict(deepcopy(self.header), dirty=self.dirty)]}
        if method == "native.snapshot.refresh":
            self.dirty = False
        if method == "native.snapshot.page":
            self.calls.append((method, params))
            return dict(deepcopy(self.header), records=deepcopy(self.rows), nextOffset=None)
        return super().call(method, params, **kwargs)


def test_lease_only_emits_after_changes_settle_and_does_not_refresh_while_quiet():
    core = ChangeCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    now = [0.0]
    lease._changes = NativeSnapshotChanges(clock=lambda: now[0])
    received = []
    lease.add_event_handler("event.inventory.snapshot", received.append)
    lease.start_capture(profile="inventory")
    try:
        assert lease.status()["native_snapshot_ready"]
        assert len(received) == 1
        for value in (1.0, 2.0, 3.0):
            now[0] = value
            assert lease.status()["native_snapshot_ready"]
        assert len(received) == 1
        assert sum(method == "native.snapshot.refresh" for method, _ in core.calls) == 1
        core.header["revision"], core.dirty = "2", True
        now[0] = 4
        assert not lease.status()["native_snapshot_ready"]
        assert len(received) == 1
        now[0] = 5
        assert lease.status()["native_snapshot_ready"]
        assert len(received) == 2
        assert received[-1]["params"]["native_snapshot"]["revision"] == "2"
    finally:
        lease.close()
        session.close()


def test_change_after_page_prevents_lease_publishing_stale_inventory():
    core = ChangeCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    received = []
    lease.add_event_handler("event.inventory.snapshot", received.append)
    lease.start_capture(profile="inventory")
    def changed(_offset, _page):
        core.header["revision"], core.dirty = "2", True
    core.on_page = changed
    try:
        assert not lease.status()["native_snapshot_ready"]
        assert received == []
    finally:
        lease.close()
        session.close()


def test_save_confirmation_rejects_late_revision_without_reading_again():
    core = ChangeCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    received = []
    lease.add_event_handler("event.inventory.snapshot", received.append)
    lease.start_capture(profile="inventory")
    try:
        assert lease.status()["native_snapshot_ready"]
        metadata = received[-1]["params"]["native_snapshot"]
        core.calls.clear()
        assert lease.confirm_inventory_snapshot(metadata)
        assert [method for method, _ in core.calls] == ["native.snapshot.status"]
        core.header["revision"], core.dirty = "2", True
        core.calls.clear()
        assert not lease.confirm_inventory_snapshot(metadata)
        assert [method for method, _ in core.calls] == ["native.snapshot.status"]
        assert len(received) == 1 and not lease.snapshot_ready
    finally:
        lease.close()
        session.close()


def test_character_only_change_does_not_invalidate_inventory_candidate():
    from tests.test_native_shared_baseline import BaselineCore
    core = BaselineCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    received = []
    lease.add_event_handler("event.inventory.snapshot", received.append)
    lease.start_capture(profile="inventory")
    try:
        assert lease.status()["native_snapshot_ready"]
        core.revisions["character"] = "2"
        core.calls.clear()
        assert lease.confirm_inventory_snapshot(received[-1]["params"]["native_snapshot"])
        assert [method for method, _ in core.calls] == ["native.snapshot.status"]
    finally:
        lease.close()
        session.close()


def test_inventory_status_waits_for_whole_battle_freeze_on_shared_session_scope():
    entered, release, inventory_blocked = Event(), Event(), Event()
    wire, failures, results = [], [], {}
    domains = ("character", "inventory", "team", "environment")

    from tests.test_native_shared_baseline import BaselineCore
    class ConcurrentCore(BaselineCore):
        armed = False

        def call(self, method, params, **kwargs):
            if self.armed:
                wire.append((current_thread().name, method))
                if method == "native.snapshot.refresh" and not entered.is_set():
                    entered.set()
                    assert release.wait(2), "test did not release the frozen snapshot RPC"
            return super().call(method, params, **kwargs)

    core = ConcurrentCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    inventory = session.inventory_client()
    inventory.start_capture(profile="inventory")
    assert inventory.status()["native_snapshot_ready"]
    battle = session.battle_client()
    battle.start_capture(profile="combat")
    original_lock = session._snapshot_lock

    class ObservedLock:
        # Instrument the actual session RLock; contention is signaled after a
        # failed acquisition, so the assertion never depends on a scheduler sleep.
        def acquire(self, *args, **kwargs):
            if current_thread().name == "automatic-inventory":
                if original_lock.acquire(blocking=False):
                    return True
                inventory_blocked.set()
            return original_lock.acquire(*args, **kwargs)

        def release(self):
            original_lock.release()

    session._snapshot_lock = ObservedLock()
    core.armed = True

    def freeze():
        try:
            results["battle"] = battle.observe_battle_scopes({"native_capture": {"scopeAttempts": {
                "combat": {"attemptId": "1", "firstChanges": {
                    d: {"revision": "1", "dirty": False} for d in domains}, "changes": []}}}})
        except Exception as error:
            failures.append(error)

    def poll():
        try:
            results["inventory"] = inventory.status()
        except Exception as error:
            failures.append(error)

    starter = Thread(target=freeze, name="battle-freeze")
    poller = Thread(target=poll, name="automatic-inventory")
    try:
        starter.start()
        assert entered.wait(1)
        poller.start()
        assert inventory_blocked.wait(1), "inventory status did not wait for the shared snapshot scope"
        assert wire == [("battle-freeze", "native.snapshot.status"), ("battle-freeze", "native.snapshot.refresh")]
        release.set()
        starter.join(2)
        poller.join(2)
        assert not starter.is_alive() and not poller.is_alive()
        assert failures == []
        frozen = results["battle"]["scopes"]["combat"]["snapshot"]
        assert frozen["state"] == "observed" and set(frozen["domains"]) == set(domains)
        assert results["inventory"]["native_snapshot_ready"]
        owners = [owner for owner, _method in wire]
        first_poll = owners.index("automatic-inventory")
        assert set(owners[:first_poll]) == {"battle-freeze"}
        assert set(owners[first_poll:]) == {"automatic-inventory"}
        assert all(method == "native.snapshot.status" for owner, method in wire if owner == "automatic-inventory")
        assert not core.aborted and not core.closed
    finally:
        release.set()
        if starter.ident is not None:
            starter.join(2)
        if poller.ident is not None:
            poller.join(2)
        core.armed = False
        inventory.close()
        battle.close()
        session.close()
