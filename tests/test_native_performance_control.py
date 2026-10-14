# 复现性能启用在等待会话锁期间撤权或换代，全部 Core 调用由内存假对象承担。
from threading import Event, Thread
from pathlib import Path

import pytest

from src.services.native_session.session import NativeGameSession
from tests.test_native_game_session import FakeNativeCore
from src.integrations.nte_core_protocol import NteCoreError, NteCoreRpcError
from src.services.performance.monitor import PerformanceMonitor

NTE_TEST_TIER = "core"


class WaitingLock:
    def __init__(self, lock, reached):
        self.lock, self.reached = lock, reached

    def __enter__(self):
        self.reached.set()  # 首次 guard 已返回，线程正要等待真实 RLock。
        self.lock.acquire()
        return self

    def __exit__(self, *_error):
        self.lock.release()


class PerformanceCore(FakeNativeCore):
    def __init__(self):
        super().__init__()
        self.hello_result["capabilities"].extend(["native_performance_v1", "native_performance_trace_v1"])
        self.configure_reply = None
        self.trace_reply = None

    def call(self, method, params=None, **kwargs):
        if method not in {"native.performance.configure", "native.performance.trace"}:
            return super().call(method, params, **kwargs)
        self.calls.append((method, dict(params)))
        reply = self.configure_reply if method == "native.performance.configure" else self.trace_reply
        if isinstance(reply, Exception):
            raise reply
        if reply is not None:
            return reply
        if method == "native.performance.trace":
            return {"trace_id": params["trace_id"], "active": False}
        return {**params, "installed": True, "rendered": False, "rejected": False,
                "cost_coverage": "native_dispatch_and_hud_v1",
                "cost_complete": False, "cost_us": None}


@pytest.mark.parametrize("change", ["permission", "context"])
def test_waiting_performance_enable_rechecks_frozen_request(change):
    admitted = Event()
    allowed = [True]
    context = [("account-a", 1)]
    core = PerformanceCore()
    starts, failures = [], []

    def guard(capability):
        if capability == "native_load":
            if not allowed[0]:
                raise PermissionError("synthetic mode revoked")

    def factory():
        starts.append(context[0])
        return core

    session = NativeGameSession(factory, guard, context_key=lambda: context[0])
    frozen_key = context[0]
    payload = {"enabled": True, "overlay": True,
               "fps_milli": None, "frame_us": None, "low_milli": None}

    def dispatch():
        try:
            session.configure_performance(payload, frozen_key)
        except Exception as error:
            failures.append(error)

    worker = Thread(target=dispatch, daemon=True)
    original_lock = session._lock
    session._lock = WaitingLock(original_lock, admitted)
    original_lock.acquire()
    locked = True
    try:
        worker.start()
        assert admitted.wait(2), "worker never reached its first permission check"
        if change == "permission":
            allowed[0] = False
        else:
            context[0] = ("account-b", 2)
        original_lock.release()
        locked = False
        worker.join(3)
        assert not worker.is_alive()
        assert not starts, "stale request opened a connection"
        assert not [row for row in core.calls if row[0] == "native.performance.configure"]
        assert len(failures) == 1
        assert isinstance(failures[0], PermissionError if change == "permission" else LookupError)
    finally:
        if locked:
            original_lock.release()
        worker.join(3)
        session.close()


def performance_session():
    core = PerformanceCore()
    state = {"key": ("account-a", 1), "allowed": True, "next_client": core}
    starts, guards = [], []

    def guard(capability):
        guards.append(capability)
        if capability == "native_load" and not state["allowed"]:
            raise PermissionError("synthetic mode revoked")

    def factory():
        starts.append(state["key"])
        return state["next_client"]

    return NativeGameSession(factory, guard, context_key=lambda: state["key"]), core, state, starts, guards


def config_payload(enabled=True):
    return {"enabled": enabled, "overlay": enabled,
            "fps_milli": None, "frame_us": None, "low_milli": None}


@pytest.mark.parametrize("permanent", [False, True])
def test_waiting_enable_stops_after_close_request(permanent):
    session, core, state, starts, _guards = performance_session()
    admitted, failures = Event(), []
    key, lock = state["key"], session._lock
    session._lock = WaitingLock(lock, admitted)

    def dispatch():
        try:
            session.configure_performance(config_payload(), key)
        except Exception as error:
            failures.append(error)

    worker = Thread(target=dispatch, daemon=True)
    lock.acquire()
    try:
        worker.start()
        assert admitted.wait(2)
        session.request_close(permanent=permanent)
    finally:
        lock.release()
        worker.join(3)
    try:
        assert not worker.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], LookupError)
        assert not starts and not core.calls
    finally:
        session.close()


def test_revocation_during_connect_is_checked_before_rpc():
    session, core, state, starts, _guards = performance_session()
    start = core.start

    def revoke_during_start():
        start()
        state["allowed"] = False

    core.start = revoke_during_start
    try:
        with pytest.raises(PermissionError):
            session.configure_performance(config_payload(), state["key"])
        assert starts == [("account-a", 1)]
        assert not [row for row in core.calls if row[0] == "native.performance.configure"]
        assert session._performance_owner is None
    finally:
        session.close()


def test_late_performance_status_rejects_account_change():
    session, core, state, starts, _guards = performance_session()
    session.configure_performance(config_payload(), state["key"])
    entered, release, failures = Event(), Event(), []

    def delayed_status():
        entered.set()
        assert release.wait(3)
        return {"native_status": {"ready": True}}

    def read():
        try:
            session.read_performance_status()
        except Exception as error:
            failures.append(error)

    core.status = delayed_status
    worker = Thread(target=read, daemon=True)
    try:
        worker.start()
        assert entered.wait(2)
        state["key"] = ("account-b", 2)
    finally:
        release.set()
        worker.join(3)
    try:
        assert not worker.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], LookupError)
        assert starts == [("account-a", 1)]
    finally:
        session.close()


@pytest.mark.parametrize("reply", [
    TimeoutError("synthetic unknown timeout"),
    NteCoreRpcError({"code": -32000, "message": "synthetic_unknown"}),
    {"enabled": True},
])
def test_unknown_configuration_retains_owner_until_valid_stop(reply):
    session, core, state, starts, _guards = performance_session()
    key = state["key"]
    core.configure_reply = reply
    try:
        with pytest.raises((TimeoutError, NteCoreRpcError, ValueError)):
            session.configure_performance(config_payload(), key)
        assert session._performance_owner == (core, key)
        assert session._performance_active is False
        assert session.close_if_idle() is False
        with pytest.raises(NteCoreError):
            with session.plugin_maintenance():
                pytest.fail("unknown performance owner entered maintenance")
        assert session._performance_owner == (core, key) and core.is_running
        assert len([row for row in core.calls if row[0] == "native.performance.configure"]) == 1
        core.configure_reply = None
        result = session.configure_performance(config_payload(False), key)
        assert result["enabled"] is False
        assert session._performance_owner is None and not session._performance_active
        assert starts == [key] and session.close_if_idle() is True
    finally:
        session.close()


def test_new_capability_rejection_does_not_release_previous_owner():
    session, core, state, _starts, _guards = performance_session()
    key = state["key"]
    try:
        session.configure_performance(config_payload(), key)
        core.hello_result["capabilities"].remove("native_performance_v1")
        with pytest.raises(NotImplementedError):
            session.configure_performance(config_payload(), key)
        assert session._performance_owner == (core, key)
        assert session._performance_active and not session.close_if_idle()
        assert len([row for row in core.calls if row[0] == "native.performance.configure"]) == 1
    finally:
        session.close()


def test_disable_after_revocation_only_cleans_original_connection():
    session, core, state, starts, guards = performance_session()
    key = state["key"]
    try:
        session.configure_performance(config_payload(), key)
        state.update(key=("account-b", 2), allowed=False)
        guards.clear()
        result = session.configure_performance(config_payload(False), key)
        assert result["enabled"] is False and not guards
        assert starts == [key]
        assert session._performance_owner is None
        assert [params["enabled"] for method, params in core.calls
                if method == "native.performance.configure"] == [True, False]
    finally:
        session.close()


def test_disable_without_connection_never_connects_or_checks_enable_permission():
    session, core, state, starts, guards = performance_session()
    state["allowed"] = False
    try:
        assert session.configure_performance(config_payload(False), state["key"])["enabled"] is False
        assert not starts and not guards and not core.calls
    finally:
        session.close()


def test_old_disable_does_not_follow_same_key_reconnection():
    session, core, state, starts, _guards = performance_session()
    key = state["key"]
    replacement = PerformanceCore()
    try:
        session.configure_performance(config_payload(), key)
        core.is_running = False
        state["next_client"] = replacement
        session.inspect(refresh=False, check_equipment=False)
        assert session._client is replacement and starts == [key, key]
        assert session._performance_owner is None and not session._performance_active
        with pytest.raises(LookupError, match="所属连接已改变"):
            session.configure_performance(config_payload(False), key)
        assert not [row for row in replacement.calls if row[0] == "native.performance.configure"]
        assert session._performance_owner is None and not session._performance_active
    finally:
        session.close()


def test_unknown_trace_start_is_not_resent_and_stop_stays_with_original_client():
    session, core, state, _starts, _guards = performance_session()
    key = state["key"]
    payload = {"action": "start", "trace_id": "original", "services": 1}
    core.trace_reply = TimeoutError("synthetic unknown start")
    replacement = PerformanceCore()
    try:
        with pytest.raises(TimeoutError):
            session.control_performance_trace(payload, key)
        assert session._performance_trace_active and not session.close_if_idle()
        assert session._performance_trace_owner == (core, "original")
        assert [params["action"] for method, params in core.calls
                if method == "native.performance.trace"] == ["start"]
        replacement.start()
        session._client = replacement
        with pytest.raises(LookupError, match="采样所属连接已改变"):
            session.control_performance_trace({**payload, "action": "stop"}, key)
        assert not replacement.calls
        session._client = core
        core.trace_reply = None
        assert session.control_performance_trace({**payload, "action": "stop"}, key)["active"] is False
        assert not session._performance_trace_active
        assert [params["action"] for method, params in core.calls
                if method == "native.performance.trace"] == ["start", "stop"]
    finally:
        session._client = core
        session.close()


def test_known_unexecuted_trace_start_rejection_releases_idle_lease():
    session, core, state, _starts, _guards = performance_session()
    core.trace_reply = NteCoreRpcError({"code": -32001, "message": "performance_busy"})
    try:
        with pytest.raises(NteCoreRpcError):
            session.control_performance_trace(
                {"action": "start", "trace_id": "rejected", "services": 1}, state["key"])
        assert session._performance_trace_active is False
        assert session.close_if_idle() is True
    finally:
        session.close()


def test_monitor_passes_frozen_key_instead_of_current_account(tmp_path):
    session, core, state, starts, _guards = performance_session()
    admitted = Event()
    key, lock = state["key"], session._lock
    session._lock = WaitingLock(lock, admitted)
    lock.acquire()
    monitor = PerformanceMonitor(lambda: ("synthetic", {}), control=session.configure_performance)
    try:
        monitor.configure(key=key, log_dir=Path(tmp_path), raw=False, allowed=True)
        monitor.set_enabled(True)
        assert admitted.wait(2)
        state.update(key=("account-b", 2), allowed=False)
        monitor.configure(key=state["key"], log_dir=Path(tmp_path), raw=False, allowed=False)
    finally:
        lock.release()
        monitor.close()
    try:
        assert not monitor._thread.is_alive()
        assert not starts and not core.calls
    finally:
        session.close()
