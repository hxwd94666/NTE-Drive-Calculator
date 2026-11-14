# 验证未登录等待只进详细日志，真实故障保留且不改变原生读取与退避行为。
from collections import Counter
from copy import deepcopy

import pytest
from loguru import logger
from PySide6.QtCore import QObject, Signal

from src.integrations.native_core import native_transport_diagnostics as diagnostics
from src.integrations.native_core.snapshots.native_inventory_snapshot import NativeSnapshotPending
from src.services.native_session.inventory import NativeSnapshotChanges
from src.ui.main_window.qt_log_sink import QtLogSink
from tests.native_session.test_native_game_session import make_session


NTE_TEST_TIER = 'core'


class GuiLogs(QObject):
    message = Signal(str)


def waiting_header(domain='inventory'):
    return dict(providerId='fixture', domain=domain, snapshotId='1', generation='1',
        domainKey='fixture', revision='1', observedUnixUs='1', observedMonotonicMs='1',
        enabled=True, ready=True, state='pending', dirty=True, recordCount=0,
        sourceRecordCount=0, enumerationComplete=False, complete=False,
        sourceCoverage='unknown', changeCoverage='unknown', failed=True, truncated=False,
        collectionComplete=False, characterRefsComplete=False,
        missing=['equip_container_unavailable', 'private diagnostic detail'],
        collectionWork=dict(version=1, steps=1, scanned=0, verified=0, elapsed_us=6330,
                            active_us=4743, max_step_us=4743, rows_read=0))


def simulate_waiting(monkeypatch, *, rounds=12):
    session, clients = make_session()
    session.inspect()
    client = clients[0]
    client.hello_result['capabilities'] += [
        'native_inventory_dto_v1', 'native_character_profile_v1', 'snapshot.changes.v1']
    status = dict(providerId='fixture', domains=[
        dict(domain=domain, domainKey='fixture', revision='1', ready=True, dirty=True, enabled=True)
        for domain in ('inventory', 'character')])
    calls, now, pending = Counter(), [0.0], 0

    def call(method, params=None, **kwargs):
        calls[method] += 1
        if method == 'native.snapshot.status':
            return deepcopy(status)
        if method == 'native.snapshot.refresh':
            return waiting_header(params['domain'])
        # 除状态和只读刷新之外的任何派发都视作测试失败。
        pytest.fail(f'unexpected RPC: {method}')

    original_call = client.call
    monkeypatch.setattr(client, 'call', call)
    lease = session.inventory_client()
    lease._changes = NativeSnapshotChanges(clock=lambda: now[0])
    try:
        lease.start_capture(profile='inventory')
        for _ in range(rounds):
            for domain in ('inventory', 'character'):
                with pytest.raises(NativeSnapshotPending):
                    lease._refresh_domain(domain, status)
                pending += 1
                # 同一退避窗口内不增加刷新或提交；新修订仍沿用原有门禁。
                before = calls.copy()
                assert lease._refresh_domain(domain, status) is None
                assert calls == before
            now[0] += 10.1
        assert not lease.snapshot_ready and client.is_running
        assert not client.closed and not client.aborted
        return dict(calls=calls, pending=pending, ready=lease.snapshot_ready)
    finally:
        # 仅回收本例创建的 owner；收尾不在上述只读 RPC 断言范围。
        monkeypatch.setattr(client, 'call', original_call)
        lease.close()
        session.close()


def test_unlogged_in_retries_are_detail_only_in_real_file_and_gui(monkeypatch, tmp_path):
    normal, detail = tmp_path / 'normal.log', tmp_path / 'detail.log'
    gui, bridge = [], GuiLogs()
    bridge.message.connect(gui.append)
    filters = lambda record: record['extra'].get('event', '').startswith('native_sync.')
    sinks = [logger.add(str(normal), level='INFO', format='{message}', filter=filters, encoding='utf8'),
             logger.add(str(detail), level='DEBUG', format='{message}', filter=filters, encoding='utf8'),
             logger.add(QtLogSink(bridge.message), level='INFO', format='{message}', filter=filters)]
    try:
        result = simulate_waiting(monkeypatch)
    finally:
        for sink in sinks:
            logger.remove(sink)
    assert result == dict(calls={'native.snapshot.status': 24, 'native.snapshot.refresh': 24},
                          pending=24, ready=False)
    assert normal.read_text(encoding='utf8') == '' and gui == []
    text = detail.read_text(encoding='utf8')
    for event in ('refresh_finished', 'projection_read', 'retry_deferred'):
        assert text.count(f'native_sync.{event} |') == 24
    assert 'private diagnostic detail' not in text


def test_refresh_integrity_faults_warn_once_until_recovery_and_stay_bounded(monkeypatch):
    events = []
    monkeypatch.setattr(diagnostics, 'log_event', lambda *args, **fields: events.append((args, fields)))
    observer = diagnostics.SnapshotReadLog()
    header = waiting_header()
    header['missing'] = ['duplicate_inventory_map_uid', 'private detail']
    for job in range(100):
        observer.refresh(dict(header, diagnosticJob=job), {'domain': 'inventory'}, 40 + job)
    warnings = [(args, fields) for args, fields in events if args[0] == 'WARNING']
    assert len(warnings) == 1 and warnings[0][1]['reason_codes'] == ['duplicate_inventory_map_uid']
    # 等待不清除真实故障去重；只有完整成功读取才重新打开同类故障记录。
    observer.refresh(waiting_header(), {'domain': 'inventory'}, 30)
    observer.refresh(header, {'domain': 'inventory'}, 40)
    assert sum(args[0] == 'WARNING' for args, _ in events) == 1
    observer.refresh(dict(header, ready=True, complete=True, failed=False, truncated=False,
                          enumerationComplete=True, missing=[]), {'domain': 'inventory'}, 40)
    observer.refresh(header, {'domain': 'inventory'}, 40)
    assert sum(args[0] == 'WARNING' for args, _ in events) == 2
    observer.refresh(header, {'domain': 'inventory', 'scope': 'all_items'}, 40)
    observer.refresh(dict(header, domain='character'), {'domain': 'character'}, 40)
    assert sum(args[0] == 'WARNING' for args, _ in events) == 4
    assert 'private' not in repr(events)


@pytest.mark.parametrize('code', ['NATIVE_MAPPING_UNSUPPORTED', 'NATIVE_CAPABILITY_MISSING',
                                 'NteCoreProtocolError', 'NteCoreTimeoutError'])
def test_read_faults_are_not_hidden_by_detail_level(monkeypatch, code):
    events = []
    monkeypatch.setattr(diagnostics, 'log_event', lambda *args, **fields: events.append((args, fields)))
    observer = diagnostics.SnapshotReadLog()
    fields = dict(completed=False, duration_ms=10, failure_code=code, failure_stage='refresh',
                  stage_duration_ms={'refresh': 10}, stage_call_count={'refresh': 1})
    for _ in range(20):
        observer.projection('inventory', **fields)
    assert sum(args[0] == 'WARNING' for args, _ in events) == 1
    observer.projection('inventory', **dict(fields, completed=True, failure_code=None))
    observer.projection('inventory', **fields)
    assert sum(args[0] == 'WARNING' for args, _ in events) == 2


@pytest.mark.parametrize('code', ['snapshot_pending', 'cancelled', 'NATIVE_SNAPSHOT_INCOMPLETE',
                                 'not_ready', 'source_changed', 'snapshot_not_found', 'disabled',
                                 'control_timeout'])
def test_expected_read_waits_do_not_become_fault_warnings(monkeypatch, code):
    events = []
    monkeypatch.setattr(diagnostics, 'log_event', lambda *args, **fields: events.append((args, fields)))
    observer = diagnostics.SnapshotReadLog()
    observer.projection('character', completed=False, duration_ms=20, failure_code=code,
        failure_stage='refresh', stage_duration_ms={'refresh': 20}, stage_call_count={'refresh': 1})
    assert events and all(args[0] == 'DEBUG' for args, _ in events)


def test_new_session_does_not_inherit_old_fault_suppression(monkeypatch):
    events = []
    monkeypatch.setattr(diagnostics, 'log_event', lambda *args, **fields: events.append((args, fields)))
    session, clients = make_session()
    header = dict(waiting_header(), truncated=True)
    try:
        for _ in range(2):
            session.inspect()
            observer = session._snapshot_read_log
            observer.refresh(header, {'domain': 'inventory'}, 20)
            observer.refresh(header, {'domain': 'inventory'}, 20)
            assert session.close_if_idle()
        assert len(clients) == 2
        assert sum(args[0] == 'WARNING' for args, _ in events) == 2
    finally:
        session.close()


@pytest.mark.parametrize('kind', ['protocol', 'mapping', 'transport', 'cancelled'])
def test_actual_read_chain_preserves_error_and_records_only_real_faults(monkeypatch, kind):
    from src.integrations.native_core.nte_core_protocol import (
        NteCoreProtocolError, NteCoreRpcError, NteCoreTimeoutError)
    from src.services.inventory.sync.capture_wait import InventorySyncCancelled

    errors = dict(protocol=NteCoreProtocolError('private detail'),
        mapping=NteCoreRpcError({'code': -32001, 'message': 'private detail',
                                'data': {'domain_code': 'NATIVE_MAPPING_UNSUPPORTED'}}),
        transport=NteCoreTimeoutError('native.snapshot.refresh', 65),
        cancelled=InventorySyncCancelled('private detail'))
    error, events = errors[kind], []
    monkeypatch.setattr(diagnostics, 'log_event', lambda *args, **fields: events.append((args, fields)))
    session, clients = make_session()
    session.inspect()
    client = clients[0]
    client.hello_result['capabilities'].append('native_inventory_dto_v1')
    original_call = client.call

    def fail_refresh(method, *args, **kwargs):
        if method == 'native.snapshot.refresh':
            raise error
        return original_call(method, *args, **kwargs)

    monkeypatch.setattr(client, 'call', fail_refresh)
    try:
        with pytest.raises(type(error)) as caught:
            session._read_projection(client, 'inventory')
        assert caught.value is error
        warnings = [(args, fields) for args, fields in events if args[0] == 'WARNING']
        assert len(warnings) == (0 if kind == 'cancelled' else 1)
        assert 'private detail' not in repr(events)
        if kind == 'mapping':
            assert client.is_running and not client.closed and not client.aborted
        elif kind in {'transport', 'cancelled'}:
            assert client.aborted
    finally:
        session.close()
