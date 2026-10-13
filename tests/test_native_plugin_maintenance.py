# 验证不关闭 Calc 的插件维护、业务隔离、未知结果与性能排空边界。
from concurrent.futures import CancelledError
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.integrations.native_host_control import NativeHostError, NativeHostOutcomeUnknown
from src.integrations.nte_core_protocol import NteCoreError
from src.services.native_session.session import NativeGameSession
from src.services.native_session.inventory import NativeInventoryLease
from src.services.components.maintenance import NativePluginMaintenance, active_feature_update_hint
from src.services.performance_monitor import PerformanceMonitor


def setup():
    core = Mock(is_running=True, native_capture=True)
    core.hello_result = {'capabilities': []}
    session = NativeGameSession(factory=lambda: core, guard=lambda _: None)
    session._connect()
    sync, performance, reconnect = Mock(), Mock(), Mock()
    performance.wait_plugin_update_idle.return_value = True
    owner = NativePluginMaintenance(session=session, sync=sync, performance=performance,
                                    check_blockers=lambda: None, reconnect=reconnect)
    return owner, session, core, sync, performance, reconnect


def run(owner, update, guard=lambda _: None):
    return owner.run(application_root='fixture', directory='fixture', game_pid=1,
                     operation_guard=guard, update=update)


def test_update_drains_existing_connection_and_resumes_without_closing_application():
    owner, session, core, sync, perf, reconnect = setup()
    session._performance_active = True
    session._hud_applied = (core, {'cooldown': True}, {})
    owner.begin()
    sync.suspend_for_plugin_update.assert_called_once()
    perf.suspend_for_plugin_update.assert_called_once()
    with pytest.raises(NteCoreError, match='组件正在更新'):
        session._connect()
    def update(**_):
        core.close.assert_called_once()
        assert session.maintenance_active
        return SimpleNamespace(state='updated')
    assert run(owner, update).state == 'updated'
    assert owner.finish(restore=True)
    assert not session.maintenance_active
    sync.resume_after_plugin_update.assert_called_once_with(restore=True)
    perf.resume_after_plugin_update.assert_called_once_with(restore=True)
    reconnect.assert_called_once()


@pytest.mark.parametrize('pending', [False, True])
def test_battle_is_never_stopped_for_update(pending):
    owner, session, core, sync, perf, _ = setup()
    if pending:
        session._battle_requested.set()
    else:
        session._lease = object()
    with pytest.raises(NteCoreError, match='战报'):
        owner.begin()
    assert not session.maintenance_active
    core.close.assert_not_called()
    sync.suspend_for_plugin_update.assert_not_called()
    perf.suspend_for_plugin_update.assert_not_called()


@pytest.mark.parametrize('unknown', [False, True])
def test_failed_update_only_restores_when_outcome_known(unknown):
    owner, session, _, sync, _, reconnect = setup()
    owner.begin()
    error = NativeHostOutcomeUnknown('pending') if unknown else NativeHostError('busy')
    update = Mock(side_effect=error)
    with pytest.raises(type(error)):
        run(owner, update)
    assert owner.finish(restore=True) is not unknown
    assert session.maintenance_active is unknown
    assert update.call_count == 1
    if unknown:
        reconnect.assert_not_called()
        sync.resume_after_plugin_update.assert_not_called()


def test_generation_cancellation_before_mutation_does_not_reconnect_old_account():
    owner, session, _, sync, perf, reconnect = setup()
    owner.begin()
    update = Mock()
    with pytest.raises(CancelledError):
        run(owner, update, guard=Mock(side_effect=CancelledError()))
    owner.finish(restore=False)
    update.assert_not_called()
    reconnect.assert_not_called()
    sync.resume_after_plugin_update.assert_called_once_with(restore=False)
    perf.resume_after_plugin_update.assert_called_once_with(restore=False)
    assert not session.maintenance_active


def test_inventory_lease_must_finish_before_disconnect():
    owner, session, core, *_ = setup()
    inventory = NativeInventoryLease(session, core)
    session._inventory_lease = inventory
    owner.begin()
    with pytest.raises(NteCoreError, match='收尾'):
        owner.lease.disconnect(check=lambda: None, timeout=.01)
    core.close.assert_not_called()
    inventory.close()
    owner.lease.disconnect(check=lambda: None)
    core.close.assert_called_once()
    owner.finish(restore=True)


@pytest.mark.parametrize('field,value', [('_equipment_context', ('provider', 'domain')),
                                       ('_write_outcome_unknown', True)])
def test_active_or_unknown_equipment_write_blocks_maintenance(field, value):
    owner, session, core, *_ = setup()
    inventory = NativeInventoryLease(session, core)
    setattr(inventory, field, value)
    session._inventory_lease = inventory
    with pytest.raises(NteCoreError, match='游戏配装'):
        owner.begin()
    core.close.assert_not_called()
    assert not session.maintenance_active


@pytest.mark.parametrize('owner,label', [('battle_report', '战报'), ('automatic_equipment_apply', '自动装配'),
    ('rewind_execution', '倒带'), ('scanning', '背包扫描'), ('character_profile_sync', '角色养成同步')])
def test_update_hint_identifies_the_blocking_feature(owner, label):
    assert label in active_feature_update_hint(owner)
    assert '请先结束当前操作' not in active_feature_update_hint(owner)


def test_delayed_hud_apply_cannot_reenable_during_maintenance():
    owner, session, core, *_ = setup()
    owner.begin()
    with pytest.raises(NteCoreError, match='HUD 暂时暂停'):
        session.configure_hud({'cooldown': True}, connect=False)
    core.call.assert_not_called()
    owner.finish(restore=True)


def test_monitor_waits_for_inflight_read_and_keeps_latest_user_preference(tmp_path):
    entered, release = Event(), Event()
    def read():
        entered.set()
        release.wait(2)
        return 'source', {}
    monitor = PerformanceMonitor(read)
    try:
        monitor.configure(key=('a', 1), log_dir=tmp_path, raw=False, allowed=True)
        monitor.set_enabled(True)
        assert entered.wait(1)
        monitor.set_maintenance(True)
        assert not monitor.wait_maintenance_idle(.01)
        monitor.set_enabled(False)  # The user changes their mind while paused.
        release.set()
        assert monitor.wait_maintenance_idle(2)
        monitor.set_maintenance(False)
        assert not monitor.snapshot()['enabled']
        assert monitor.snapshot()['rows'] == {}
    finally:
        release.set()
        monitor.close()
