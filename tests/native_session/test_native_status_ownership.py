# 验证共享状态读取合并、连接隔离及业务忙碌不会撤销背包租约。
from threading import Event, Thread

import pytest

from src.integrations.nte_core_protocol import NteCoreRpcError, NteCoreProcessError
from src.services.inventory.sync.capture_wait import InventorySyncCancelled
from src.services.native_session.session import NativeGameSession
from tests.test_native_game_session import FakeNativeCore


def inventory_session():
    core = FakeNativeCore()
    core.hello_result['capabilities'].append('native_inventory_dto_v1')
    session = NativeGameSession(lambda: core, lambda _cap: None)
    return session, core, session.inventory_client()


def test_busy_background_inspection_preserves_inventory_and_connection():
    session, core, lease = inventory_session()
    error = NteCoreRpcError({'code': -32000, 'message': 'native_status_pending',
                            'data': {'domain_code': 'REQUEST_IN_PROGRESS'}})
    core.status = lambda: (_ for _ in ()).throw(error)
    try:
        with pytest.raises(NteCoreRpcError):
            session.inspect(refresh=False, check_equipment=False)
        assert not core.closed and not session._failed
        assert lease.start() is lease
    finally:
        lease.close()
        session.close()


def test_performance_query_joins_inflight_inspection_without_second_rpc():
    session, core, lease = inventory_session()
    entered, release = Event(), Event()
    failures = []
    observed, completed = [], Event()

    def status():
        entered.set()
        assert release.wait(3)
        return {'native_status': {'ready': True}}

    def inspect():
        try:
            session.inspect(refresh=False, check_equipment=False)
        except Exception as error:
            failures.append(error)

    core.status = status
    original_call = core.call
    core.call = lambda method, params=None, **kw: original_call(method, params, **kw)
    worker = Thread(target=inspect)
    worker.start()
    def observe():
        try:
            observed.append(session.read_performance_status())
        except Exception as error:
            failures.append(error)
        finally:
            completed.set()
    observer = Thread(target=observe)
    try:
        assert entered.wait(2)
        observer.start()
        assert not completed.wait(.05)
        assert sum(method == 'core.status' for method, _ in core.calls) == 1
        assert not core.closed
    finally:
        release.set()
        worker.join(3)
        observer.join(3)
        lease.close()
        session.close()
    assert not worker.is_alive() and not observer.is_alive() and not failures
    assert observed == [(core, {'native_status': {'ready': True}})]


def test_new_connection_does_not_join_old_inflight_status():
    from src.services.native_session.inspection import NativeStatusQueries
    queries = NativeStatusQueries()
    old, new = FakeNativeCore(), FakeNativeCore()
    entered, release = Event(), Event()
    results = []
    def old_status():
        entered.set()
        assert release.wait(3)
        return {'source': 'old'}
    old.status = old_status
    new.status = lambda: {'source': 'new'}
    worker = Thread(target=lambda: results.append(queries.read(old)))
    worker.start()
    try:
        assert entered.wait(2)
        assert queries.read(new) == {'source': 'new'}
    finally:
        release.set()
        worker.join(3)
    assert results == [{'source': 'old'}]
    assert queries.read(new) == {'source': 'new'}
    assert len(new.calls) == 2  # A completed response is never reused as a fresh sample.


def test_trace_stop_never_follows_reconnected_client():
    from src.services.native_session.performance_control import control_performance_trace
    session, core, lease = inventory_session()
    replacement = FakeNativeCore()
    for client in (core, replacement):
        client.hello_result['capabilities'].append('native_performance_trace_v1')
    try:
        lease.start()
        context = session._current_context()
        control_performance_trace(session, {'action': 'start', 'trace_id': 'original', 'services': 1}, context)
        replacement.start()
        session._client = replacement
        with pytest.raises(LookupError, match='采样所属连接已改变'):
            control_performance_trace(session, {'action': 'stop', 'trace_id': 'original', 'services': 1}, context)
        assert not any(method == 'native.performance.trace' for method, _ in replacement.calls)
    finally:
        session._client = core
        lease.close()
        session.close()


def test_dead_transport_cancels_with_explicit_recoverable_reason():
    session, core, lease = inventory_session()
    core.status = lambda: (_ for _ in ()).throw(NteCoreProcessError('closed', return_code=1))
    try:
        with pytest.raises(NteCoreProcessError):
            session.inspect(refresh=False, check_equipment=False)
        assert core.closed
        with pytest.raises(InventorySyncCancelled) as caught:
            lease.start()
        assert caught.value.reason == 'connection_lost'
    finally:
        lease.close()
        session.close()
