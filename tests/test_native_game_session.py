# 验证原生会话单一所有者、收尾租约、故障重连及账号授权边界。
import pytest

from src.services.native_game_session import NativeGameSession, SNAPSHOT_DOMAINS
from src.integrations.nte_core_protocol import NteCoreRpcError


class FakeNativeCore:
    native_capture = True
    executable_sha256 = "test-hash"

    def __init__(self):
        self.hello_result = {"capabilities": ["native_battle_scope_snapshot_v1", *[f"{d}.snapshot.v1" for d in SNAPSHOT_DOMAINS]]}
        self.is_running = False
        self.closed = False
        self.aborted = False
        self.calls = []
        self.handlers = {}
        self.fail_status = False
        self.on_call = None

    def start(self):
        self.is_running = True

    def status(self):
        if self.fail_status:
            raise RuntimeError("failed status")
        return {"native_status": {"ready": True}}

    def call(self, method, params, **kwargs):
        self.calls.append((method, params))
        if self.on_call:
            self.on_call(method, params)
        return {"domains": []}

    def start_capture(self, **kwargs):
        self.calls.append(("capture.start", kwargs))
        return {}

    def stop_capture(self):
        self.calls.append(("capture.stop", {}))
        return {}

    def get_battle_record(self, **kwargs):
        return {"final": True}

    def get_battle_summary(self, **kwargs):
        return {}

    def get_battle_axis(self, **kwargs):
        return {}

    def add_event_handler(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def remove_event_handler(self, event, handler):
        self.handlers[event].remove(handler)

    def emit(self, event, payload):
        for handler in tuple(self.handlers.get(event, ())):
            handler(payload)

    def abort(self):
        self.aborted = True
        self.is_running = False

    def close(self):
        self.closed = True
        self.is_running = False


def make_session(*, guard=lambda _cap: None, context_key=None):
    clients = []

    def factory():
        client = FakeNativeCore()
        clients.append(client)
        return client

    return NativeGameSession(factory, guard, context_key), clients


def test_raw_snapshot_setting_configures_existing_core_before_business_requests():
    core = FakeNativeCore()
    core.hello_result['capabilities'].append('native_snapshot_archive_v1')
    enabled = [True]
    session = NativeGameSession(lambda: core, lambda _cap: None, diagnostics_enabled=lambda: enabled[0])
    try:
        session.inspect()
        assert core.calls[0] == ('native.diagnostics.configure', {'enabled': True})
        session.inspect()
        assert sum(method == 'native.diagnostics.configure' for method, _ in core.calls) == 1
        enabled[0] = False
        session.inspect()
        assert [params for method, params in core.calls if method == 'native.diagnostics.configure'] == [
            {'enabled': True}, {'enabled': False}]
    finally:
        session.close()


def test_raw_snapshot_setting_does_not_silently_claim_support_on_old_core():
    core = FakeNativeCore()
    session = NativeGameSession(lambda: core, lambda _cap: None, diagnostics_enabled=lambda: True)
    try:
        with pytest.raises(RuntimeError, match='不支持 DLL 原始快照'):
            session.inspect()
        assert not core.calls
    finally:
        session.close()


def test_raw_snapshot_write_failure_keeps_business_connection_available(monkeypatch):
    core = FakeNativeCore()
    core.hello_result['capabilities'].append('native_snapshot_archive_v1')
    warnings = []
    monkeypatch.setattr(NativeGameSession, '_archive_failed', staticmethod(lambda *_: warnings.append(True)))
    def fail_configure(method, _params):
        if method == 'native.diagnostics.configure':
            raise NteCoreRpcError({'code': -32000, 'data': {'domain_code': 'NATIVE_SNAPSHOT_ARCHIVE_WRITE_FAILED'}})
    core.on_call = fail_configure
    session = NativeGameSession(lambda: core, lambda _cap: None, diagnostics_enabled=lambda: True)
    try:
        session.inspect()
        session.inspect()
        assert core.is_running and len(warnings) == 1
        core.emit('event.native.diagnostics.error', {'code': 'NATIVE_SNAPSHOT_ARCHIVE_WRITE_FAILED'})
        assert len(warnings) == 2
    finally:
        session.close()


def test_battle_lease_reuses_sync_core_and_close_does_not_close_owner():
    session, clients = make_session()
    session.inspect(refresh=True)
    lease = session.battle_client()
    lease.start()
    lease.start_capture(profile="combat")
    lease.close()
    session.inspect()
    assert len(clients) == 1
    assert not clients[0].closed
    assert not session.battle_active


def test_owner_close_disables_domains_and_waits_for_battle_drain():
    session, clients = make_session()
    lease = session.battle_client()
    session.close()
    assert not clients[0].closed
    disabled = [params["domain"] for method, params in clients[0].calls if method == "native.snapshot.disable"]
    assert disabled == list(SNAPSHOT_DOMAINS)
    with pytest.raises(RuntimeError):
        session.battle_client()
    with pytest.raises(RuntimeError):
        session.inspect()
    with pytest.raises(RuntimeError):
        lease.start_capture(profile="combat")
    lease.stop_capture()
    assert lease.get_battle_record()["final"]
    lease.close()
    assert clients[0].closed


def test_refresh_checks_permission_for_each_domain_and_never_starts_combat():
    allowed = True

    def guard(_cap):
        if not allowed:
            raise PermissionError("revoked")

    session, clients = make_session(guard=guard)
    session.inspect()

    def revoke(method, _params):
        nonlocal allowed
        if method == "native.snapshot.refresh":
            allowed = False

    clients[0].on_call = revoke
    with pytest.raises(PermissionError):
        session.inspect(refresh=True)
    refreshed = [params for method, params in clients[0].calls if method == "native.snapshot.refresh"]
    assert refreshed == [{"domain": "character"}]
    assert not any(method == "capture.start" for method, _ in clients[0].calls)


def test_inspect_during_battle_does_not_refresh_or_abort_failed_connection():
    session, clients = make_session()
    lease = session.battle_client()
    session.inspect(refresh=True)
    assert not any(method == "native.snapshot.refresh" for method, _ in clients[0].calls)
    clients[0].fail_status = True
    with pytest.raises(RuntimeError):
        session.inspect()
    assert not clients[0].closed and not clients[0].aborted
    lease.close()
    assert clients[0].closed
    session.inspect()
    assert len(clients) == 2


def test_stale_idle_core_reconnects():
    session, clients = make_session()
    session.inspect()
    clients[0].is_running = False
    session.inspect()
    assert len(clients) == 2 and clients[0].closed


def test_failed_idle_inspect_reconnects_next_attempt():
    session, clients = make_session()
    session.inspect()
    clients[0].fail_status = True
    with pytest.raises(RuntimeError):
        session.inspect()
    session.inspect()
    assert len(clients) == 2 and clients[0].closed


def test_lease_abort_clears_bad_core_and_requires_release_before_reconnect():
    session, clients = make_session()
    lease = session.battle_client()
    lease.abort()
    assert clients[0].aborted
    with pytest.raises(RuntimeError):
        session.battle_client()
    with pytest.raises(RuntimeError):
        session.inspect()
    with pytest.raises(RuntimeError):
        lease.get_battle_axis()
    lease.close()
    session.inspect()
    assert len(clients) == 2


def test_released_callbacks_detached_and_stale_lease_cannot_affect_new_one():
    session, clients = make_session()
    old = session.battle_client()
    received = []
    old.add_event_handler("hit", received.append)
    clients[0].emit("hit", 1)
    old.close()
    current = session.battle_client()
    old.abort()
    old.close()
    clients[0].emit("hit", 2)
    assert received == [1]
    assert session.battle_active and not clients[0].aborted
    current.close()


@pytest.mark.parametrize("change_account", [False, True])
def test_callbacks_recheck_mode_and_account_context(change_account):
    context = 1
    allowed = True

    def guard(_cap):
        if not allowed:
            raise PermissionError("revoked")

    session, clients = make_session(guard=guard, context_key=lambda: context)
    lease = session.battle_client()
    received = []
    lease.add_event_handler("hit", received.append)
    clients[0].emit("hit", 1)
    if change_account:
        context = 2
    else:
        allowed = False
    clients[0].emit("hit", 2)
    assert received == [1]
    if change_account:
        with pytest.raises(RuntimeError):
            lease.get_battle_record()
    else:
        assert lease.get_battle_record()["final"]
    lease.close()


def test_released_lease_cannot_start_capture():
    session, _clients = make_session()
    lease = session.battle_client()
    lease.close()
    with pytest.raises(RuntimeError):
        lease.start_capture(profile="combat")


def test_stop_timeout_aborts_shared_core_and_releases_on_lease_close():
    from threading import Event
    from src.services.battle_capture_lifecycle import stop_capture_with_timeout

    stop_wait = Event()

    class StuckCore(FakeNativeCore):
        def stop_capture(self):
            stop_wait.wait(1)
            return {}

        def abort(self):
            super().abort()
            stop_wait.set()

    client = StuckCore()
    session = NativeGameSession(lambda: client, lambda _cap: None)
    lease = session.battle_client()
    with pytest.raises(RuntimeError, match="停止超时"):
        stop_capture_with_timeout(lease, 0.01)
    assert client.aborted
    lease.close()
    assert not session.battle_active


def test_permanent_close_request_blocks_new_core_before_background_teardown():
    session, clients = make_session()
    session.request_close(permanent=True)
    with pytest.raises(RuntimeError):
        session.inspect()
    assert clients == []
    session.close()
    with pytest.raises(RuntimeError):
        session.battle_client()


def test_temporary_close_request_allows_reconnect_after_teardown():
    session, clients = make_session()
    session.inspect()
    session.request_close()
    assert not clients[0].closed
    with pytest.raises(RuntimeError):
        session.inspect()
    session.close()
    session.inspect()
    assert len(clients) == 2


def test_close_requested_during_factory_does_not_start_core():
    client = FakeNativeCore()

    def factory():
        session.request_close(permanent=True)
        return client

    session = NativeGameSession(factory, lambda _cap: None)
    with pytest.raises(RuntimeError):
        session.inspect()
    assert not client.is_running and client.closed


@pytest.mark.parametrize("message", ["not_ready", "source_changed"])
def test_domain_business_rejection_preserves_other_domains_and_shared_battle_core(message):
    session, clients = make_session()
    session.inspect()
    client = clients[0]

    def reject_character(method, params):
        if method == "native.snapshot.refresh" and params["domain"] == "character":
            raise NteCoreRpcError({"code": -32001, "message": message, "data": {"reason": "pawn_unavailable"}})

    client.on_call = reject_character
    report = session.inspect(refresh=True)
    assert report["domain_errors"] == {"character": {
        "code": -32001, "message": message, "reason": "pawn_unavailable",
    }}
    assert report["status"]["native_status"]["ready"] is True
    refreshed = [params["domain"] for method, params in client.calls if method == "native.snapshot.refresh"]
    assert refreshed == list(SNAPSHOT_DOMAINS)
    assert client.calls[-1][0] == "native.snapshot.status"
    assert not client.closed and not client.aborted
    assert not any(method == "capture.start" for method, _ in client.calls)
    lease = session.battle_client()
    lease.start_capture(profile="combat")
    assert len(clients) == 1
    lease.close()
    assert not client.closed


@pytest.mark.parametrize("error", [
    NteCoreRpcError({"code": -32602, "message": "not_ready"}),
    NteCoreRpcError({"code": -32001, "message": "invalid_snapshot_identity"}),
    TimeoutError("pipe timed out"),
])
def test_domain_protocol_and_transport_faults_still_invalidate_idle_core(error):
    session, clients = make_session()
    session.inspect()

    def fail_refresh(method, _params):
        if method == "native.snapshot.refresh":
            raise error

    clients[0].on_call = fail_refresh
    with pytest.raises(type(error)):
        session.inspect(refresh=True)
    assert clients[0].closed
    session.inspect()
    assert len(clients) == 2
