# 覆盖共享原生会话的家具分页、修订去重、保存确认、失败重试及静默账号更新。
from copy import deepcopy
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from src.domain.official_role.models import WorldBonusSettings
from src.services.inventory.sync.furniture import apply_native_furniture
from src.services.inventory.sync.state import InventorySyncState
from src.services.native_session.inventory import NativeSnapshotChanges
from src.services.native_session.session import NativeGameSession
from src.services.official_role.panel.world_bonus import WorldBonusSettingsService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.native_session.test_native_change_sync import ChangeCore


NTE_TEST_TIER = 'core'


class FurnitureCore(ChangeCore):
    def __init__(self):
        super().__init__()
        self.environment = dict(deepcopy(self.header), domain='environment', snapshotId='2',
            domainKey='fixture-environment', recordCount=3, sourceRecordCount=3, revision='1',
            ready=True, enabled=True, dirty=False, complete=True, enumerationComplete=True,
            truncated=False, failed=False)
        self.records = [dict(kind='furniture_raw', FurnitureID=identity, Level=level, bIsActivated=True)
            for identity, level in [('SF_0011', 3), ('SF_0012', 4), ('SF_0013', 5)]]
        self.environment_changed_on_page = False

    def call(self, method, params=None, **kwargs):
        if method == 'native.snapshot.status':
            return {'providerId': 'fixture', 'domains': [deepcopy(self.header), deepcopy(self.environment)]}
        if isinstance(params, dict) and params.get('domain') == 'environment':
            self.calls.append((method, deepcopy(params)))
            if method == 'native.snapshot.refresh':
                return deepcopy(self.environment)
            if method == 'native.snapshot.page':
                result = dict(deepcopy(self.environment), records=deepcopy(self.records), nextOffset=None)
                if self.environment_changed_on_page:
                    self.environment['revision'] = '2'
                return result
        return super().call(method, params or {}, **kwargs)


@pytest.fixture
def native_lease():
    core = FurnitureCore()
    session = NativeGameSession(lambda: core, lambda _cap: None)
    lease = session.inventory_client()
    now = [0.0]
    lease._changes = NativeSnapshotChanges(clock=lambda: now[0])
    lease.start_capture(profile='inventory')
    try:
        yield core, session, lease, now
    finally:
        lease.close()
        session.close()


def refresh_count(core):
    return sum(method == 'native.snapshot.refresh' and params.get('domain') == 'environment'
               for method, params in core.calls)


def test_inventory_delivered_first_then_environment_waits_for_save_ack(native_lease):
    core, session, lease, now = native_lease
    assert 'native_furniture_snapshot' not in lease.status()
    state = lease.status()
    first = state['native_furniture_snapshot']
    assert state['native_snapshot_ready'] and refresh_count(core) == 1
    assert lease.status()['native_furniture_snapshot'] is first
    assert refresh_count(core) == 1
    lease.confirm_furniture_snapshot_saved(first)
    assert 'native_furniture_snapshot' not in lease.status()
    assert refresh_count(core) == 1
    core.environment['revision'] = '2'
    assert 'native_furniture_snapshot' not in lease.status()
    now[0] += .3
    assert lease.status()['native_furniture_snapshot']['revision'] == '2'
    assert refresh_count(core) == 2
    assert not session.battle_active
    assert not any(method.startswith(('equipment.', 'capture.')) for method, _ in core.calls)


def test_environment_changed_during_page_is_not_delivered(native_lease):
    core, session, lease, now = native_lease
    lease.status()
    core.environment_changed_on_page = True
    assert 'native_furniture_snapshot' not in lease.status()
    assert lease._furniture_pending is None
    assert not core.closed and not core.aborted


def test_unconfirmed_candidate_is_discarded_when_same_revision_becomes_dirty(native_lease):
    core, session, lease, now = native_lease
    lease.status()
    assert 'native_furniture_snapshot' in lease.status()
    core.environment['dirty'] = True
    assert 'native_furniture_snapshot' not in lease.status()
    assert lease._furniture_pending is None


def test_pending_environment_preserves_inventory_and_backs_off(native_lease):
    core, session, lease, now = native_lease
    lease.status()
    core.environment['ready'] = False
    assert lease.status()['native_snapshot_ready']
    count = refresh_count(core)
    for _ in range(20):
        assert 'native_furniture_snapshot' not in lease.status()
    assert refresh_count(core) == count
    now[0] += 1.1
    lease.status()
    assert refresh_count(core) == count + 1


def test_saving_only_furniture_advances_internal_revision_without_frontend_message(native_lease, tmp_path):
    core, session, lease, now = native_lease
    path = tmp_path / 'account.sqlite3'
    with UserDataDao(path, account_id='fixture', account_name='fixture'):
        pass
    service = SimpleNamespace(_native_furniture_apply=WorldBonusSettingsService(path).apply_native,
        _native_furniture_retry_at=0., _native_furniture_save_error=None, _stop_requested=Event(),
        _context_is_current=lambda: True, _operation_guard=lambda _cap: None, capture_source='native',
        state=InventorySyncState(phase='listening', message='unchanged'))
    publications = []
    def publish(phase, message, **fields):
        publications.append(fields)
        service.state = replace(service.state, phase=phase, message=message, **fields)
    service._publish = publish
    lease.status()
    apply_native_furniture(service, lease, lease.status())
    assert WorldBonusSettingsService(path).load() == WorldBonusSettings(6., .016, 15.)
    assert publications == [{'character_sync_revision': 1}]
    assert service.state.message == 'unchanged'
    assert 'native_furniture_snapshot' not in lease.status()


def test_source_without_environment_keeps_old_sync_behavior(native_lease):
    core, session, lease, now = native_lease
    core.hello_result['capabilities'].remove('environment.snapshot.v1')
    lease.status()
    assert 'native_furniture_snapshot' not in lease.status()
    assert refresh_count(core) == 0


def test_cancelled_current_account_never_saves_furniture(tmp_path):
    from src.services.inventory.sync.capture_wait import InventorySyncCancelled
    writes = []
    service = SimpleNamespace(_native_furniture_apply=lambda *a, **kw: writes.append(a),
        _native_furniture_retry_at=0., _stop_requested=Event(), _context_is_current=lambda: False)
    with pytest.raises(InventorySyncCancelled):
        apply_native_furniture(service, None, {'native_furniture_snapshot': {'records': []}})
    assert writes == []
