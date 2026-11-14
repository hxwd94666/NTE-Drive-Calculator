# 验证游戏轮盘动作只派发一次，原生状态确认后应答，旧账号结果不执行。
from concurrent.futures import Future
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtWidgets import QWidget
from tests.support.auto_sync_ui_fixture import application
from tests.support.auto_sync_ui_fixture import dispose
from src.features.plugins.quick_wheel_controller import QuickWheelController


def make_controller(preferences=None):
    application()
    parent = QWidget()
    context = ['account', 1]
    state = [0]
    settings = {'cooldown': False, 'enemy_bars': False}
    status = {'cooldown': '未启用', 'enemy_bars': '未启用'}
    performance = {'overlay': False, 'overlay_state': 'off', 'preference_error': ''}
    controller = QuickWheelController(
        session=Mock(), context_key=lambda: tuple(context), policy=Mock(allowed=lambda _: True),
        battle_state=lambda: state[0], start_battle=Mock(), stop_battle=Mock(),
        hud_settings=lambda: (settings, status), change_hud=Mock(),
        performance_state=lambda: performance, change_performance=Mock(), restore_window=Mock(), parent=parent, preferences=preferences)
    controller._context = tuple(context)
    if preferences is None:
        controller.enabled = True
    controller._lease_active = controller.enabled
    return SimpleNamespace(c=controller, parent=parent, context=context, state=state, performance=performance)


def deliver(s, action_id, action, context=None):
    future = Future()
    future.set_result({'owner': s.c._owner, 'ready': True, 'action_id': action_id,
                       'action': action, 'wooden': {'ownedCount': 0}})
    s.c._receive((context or tuple(s.context), {'enabled': True}, future, s.c._revision))


def cleanup(s):
    s.c.enabled = s.c._needs_disable = False
    s.c.close()
    dispose(s.parent)


def test_duplicate_and_older_actions_do_not_execute():
    s = make_controller()
    try:
        deliver(s, 2, 'open_calc')
        deliver(s, 2, 'open_calc')
        deliver(s, 1, 'open_calc')
        s.c.restore_window.assert_called_once()
        assert s.c._ack == 2
    finally:
        cleanup(s)


def test_capture_ack_waits_for_running_state():
    s = make_controller()
    s.c.start_battle.side_effect = lambda: s.state.__setitem__(0, 1)
    try:
        deliver(s, 1, 'record_start')
        deliver(s, 1, 'record_start')
        assert s.c._ack == 0 and s.c._pending == 'record_start'
        s.c.start_battle.assert_called_once()
        s.state[0] = 2
        s.c._settle_action()
        assert s.c._ack == 1 and not s.c._pending
    finally:
        cleanup(s)


def test_old_account_reply_and_disabled_reply_do_not_dispatch():
    s = make_controller()
    try:
        old_context = tuple(s.context)
        s.context[1] += 1
        deliver(s, 1, 'record_start', old_context)
        s.c.start_battle.assert_not_called()
        assert s.c.enabled  # 本机偏好保留，但旧账号动作不能移入新账号。
        old_owner = s.c._owner
        s.c._poll()
        assert s.c._context == tuple(s.context) and s.c._owner != old_owner
        s.c.enabled = False
        deliver(s, 2, 'record_start')
        s.c.start_battle.assert_not_called()
    finally:
        cleanup(s)


def test_shutdown_drains_one_worker_and_confirms_disable():
    s = make_controller()
    s.c.session.exchange_quick_wheel.return_value = {'ready': False}
    try:
        s.c.begin_close()
        deadline = monotonic() + 2
        while s.c.is_running() and monotonic() < deadline:
            application().processEvents()
            sleep(.005)
        assert not s.c.is_running()
        assert s.c.session.exchange_quick_wheel.call_args.args[0]['enabled'] is False
    finally:
        cleanup(s)


def test_performance_ack_waits_for_visible_then_off_without_touching_other_features():
    s = make_controller()
    try:
        deliver(s, 1, 'performance_on')
        deliver(s, 1, 'performance_on')
        s.c.change_performance.assert_called_once_with(True)
        assert s.c._ack == 0
        s.performance.update(overlay=True, overlay_state='waiting')
        s.c._settle_action()
        assert s.c._ack == 0
        s.performance['overlay_state'] = 'visible'
        s.c._settle_action()
        assert s.c._ack == 1 and not s.c._pending
        deliver(s, 2, 'performance_off')
        s.c.change_performance.assert_called_with(False)
        assert s.c._ack == 1
        s.performance.update(overlay=False, overlay_state='off')
        s.c._settle_action()
        assert s.c._ack == 2 and not s.c._pending
        s.c.start_battle.assert_not_called()
        s.c.change_hud.assert_not_called()
    finally:
        cleanup(s)


def test_failed_performance_action_is_acknowledged_without_replay():
    s = make_controller()
    try:
        deliver(s, 1, 'performance_on')
        s.performance.update(preference_error='设置保存失败')
        s.c._settle_action()
        assert s.c._ack == 1 and not s.c._pending
        assert s.c.status == '设置保存失败'
        deliver(s, 1, 'performance_on')
        s.c.change_performance.assert_called_once_with(True)
    finally:
        cleanup(s)


def test_preference_restores_menu_only_and_exit_does_not_clear_it(tmp_path):
    from src.integrations.preferences.json_store import LocalPreferencesStore
    store = LocalPreferencesStore(tmp_path / 'quick_wheel.json')
    store.update({'enabled': True})
    s = make_controller(store)
    try:
        assert s.c.enabled
        s.c.start_battle.assert_not_called()
        s.c.change_hud.assert_not_called()
        s.c.change_performance.assert_not_called()
        s.c.session.exchange_quick_wheel.return_value = {'ready': False}
        s.c.begin_close()
        deadline = monotonic() + 2
        while s.c.is_running() and monotonic() < deadline:
            application().processEvents()
            sleep(.005)
        assert store.read()['enabled'] is True
    finally:
        cleanup(s)
    reopened = make_controller(store)
    try:
        reopened.c.session.exchange_quick_wheel.return_value = {'ready': False}
        reopened.c.set_enabled(False)
        assert store.read()['enabled'] is False
    finally:
        deadline = monotonic() + 2
        while reopened.c._future is not None and monotonic() < deadline:
            application().processEvents()
            sleep(.005)
        cleanup(reopened)
    disabled = make_controller(store)
    try:
        assert not disabled.c.enabled
    finally:
        cleanup(disabled)


def test_disconnect_preserves_preference_but_discards_old_pending_action():
    from src.integrations.native_core.nte_core_protocol import NteCoreTimeoutError
    s = make_controller()
    try:
        deliver(s, 1, 'record_start')
        s.c._seen, s.c._ack, s.c._pending = 7, 6, 'record_start'
        owner = s.c._owner
        failure = Future()
        failure.set_exception(NteCoreTimeoutError('native.wheel.exchange', 1.5))
        s.c._receive((tuple(s.context), {'enabled': True}, failure, s.c._revision))
        assert s.c.enabled and s.c._owner != owner
        assert (s.c._ack, s.c._seen, s.c._pending) == (0, 0, '')
        assert s.c._retry_at > monotonic()
        s.c._poll()
        s.c.session.exchange_quick_wheel.assert_not_called()
        s.c.start_battle.assert_called_once()
    finally:
        cleanup(s)


def test_saved_menu_does_not_connect_while_mode_or_maintenance_disallows_it(tmp_path):
    from src.integrations.preferences.json_store import LocalPreferencesStore
    store = LocalPreferencesStore(tmp_path / 'quick_wheel.json')
    store.update({'enabled': True})
    s = make_controller(store)
    try:
        s.c._lease_active = False
        s.c.runtime_allowed = lambda: False
        s.c._poll()
        s.c.session.exchange_quick_wheel.assert_not_called()
        assert s.c.enabled and store.read()['enabled']
    finally:
        cleanup(s)


def test_new_core_connection_rejects_old_ack_before_dispatch():
    from threading import RLock
    from src.services.native_session.quick_wheel import exchange_quick_wheel, QuickWheelConnectionChanged
    import pytest
    client = Mock(is_running=True, hello_result={'capabilities': ['native_quick_wheel_v2']})
    session = SimpleNamespace(_guard=Mock(), _lock=RLock(), _current_context=lambda: ('a', 1),
                              _close_requested=SimpleNamespace(is_set=lambda: False),
                              _connect=lambda: client, _quick_wheel_client=None, _quick_wheel_active=False)
    payload = {'enabled': True, 'owner': 123, 'ack': 12}
    with pytest.raises(QuickWheelConnectionChanged):
        exchange_quick_wheel(session, payload, ('a', 1))
    client.call.assert_not_called()
    client.call.return_value = {'owner': 456, 'ready': True}
    payload.update(owner=456, ack=0)
    exchange_quick_wheel(session, payload, ('a', 1))
    assert client.call.call_args.args[1]['ack'] == 0
    replacement = Mock(is_running=True, hello_result=client.hello_result)
    session._connect = lambda: replacement
    with pytest.raises(QuickWheelConnectionChanged):
        exchange_quick_wheel(session, payload, ('a', 1))
    replacement.call.assert_not_called()


def test_account_stop_revokes_reply_before_generation_changes_and_reopens_menu_only():
    s = make_controller()
    try:
        old_owner, revision = s.c._owner, s.c._revision
        reply = Future()
        reply.set_result({'ready': True, 'action_id': 1, 'action': 'record_start'})
        s.c.stop_for_account()
        s.c._receive((tuple(s.context), {'enabled': True}, reply, revision))
        s.c._poll()
        s.c.start_battle.assert_not_called()
        s.c.session.exchange_quick_wheel.assert_not_called()
        assert s.c.enabled and s.c._owner != old_owner
        s.context[1] += 1
        s.c.rebuild_account(None)
        s.c.resume_account()
        assert s.c._timer.isActive() and s.c._context == tuple(s.context)
        assert s.c._seen == s.c._ack == 0 and not s.c._pending
        assert s.c._retry_at > monotonic()
    finally:
        cleanup(s)
