# 验证原生就绪持续等待的原因分类、取消和抓包隔离。
from threading import Event
from unittest.mock import Mock

import pytest

from src.integrations.nte_core_protocol import NteCoreProcessError, NteCoreRpcError
from src.services.battle_capture_lifecycle import start_capture_when_ready


def unready(reason):
    return NteCoreRpcError({'code': -32001, 'message': 'not_ready',
                           'data': {'reason': reason, 'sceneKind': 'unknown'}})


@pytest.mark.parametrize('reason', ['sdk_initializing', 'hook_initializing', 'game_thread_pending',
                                    'world_unavailable', 'controller_unavailable',
                                    'pawn_unavailable', 'scene_transition'])
def test_declared_transient_reason_waits_on_same_owner_and_can_be_cancelled(reason):
    stop = Event()
    request = Mock(side_effect=unready(reason))
    messages = []

    def cancel(message):
        messages.append(message)
        stop.set()

    assert not start_capture_when_ready(request, stop_event=stop,
                                       wait_for_native_ready=True, on_wait=cancel)
    assert request.call_count == 1
    assert len(messages) == 1
    assert '可随时停止' in messages[0]


@pytest.mark.parametrize('reason', ['sdk_unavailable', 'hook_unavailable', 'provider_stopping',
                                    None, 'future_reason', {'invalid': True}])
def test_hard_missing_or_unknown_reason_fails_without_retry(reason):
    request = Mock(side_effect=unready(reason))
    wait = Mock()
    with pytest.raises(NteCoreProcessError, match='采集 DLL'):
        start_capture_when_ready(request, stop_event=Event(), wait_for_native_ready=True, on_wait=wait)
    assert request.call_count == 1
    wait.assert_not_called()


def test_transient_wait_continues_beyond_previous_budgets_until_ready():
    # 180 half-second waits exceed both previous budgets; no real sleeping.
    request = Mock(side_effect=[unready('world_unavailable') for _ in range(180)] + [{}])
    stop = Mock()
    stop.is_set.return_value = False
    stop.wait.return_value = False
    messages = Mock()
    assert start_capture_when_ready(request, stop_event=stop, wait_for_native_ready=True, on_wait=messages)
    assert request.call_count == 181
    assert stop.wait.call_count == 180
    messages.assert_called_once()


def test_packet_rejection_does_not_enter_native_wait():
    original = unready('sdk_initializing')
    request = Mock(side_effect=original)
    with pytest.raises(NteCoreRpcError) as caught:
        start_capture_when_ready(request, stop_event=Event(), wait_for_native_ready=False,
                                 on_wait=Mock())
    assert caught.value is original
    assert request.call_count == 1


def test_start_succeeds_after_transient_rejection_without_new_client():
    request = Mock(side_effect=[unready('world_unavailable'), {}])
    stop = Mock()
    stop.is_set.return_value = False
    stop.wait.return_value = False
    assert start_capture_when_ready(request, stop_event=stop, wait_for_native_ready=True, on_wait=Mock())
    assert request.call_count == 2
    stop.wait.assert_called_once()


def test_cancellation_while_start_is_rejected_does_not_surface_readiness_error():
    stop = Event()

    def request():
        stop.set()
        raise unready('sdk_unavailable')

    assert not start_capture_when_ready(request, stop_event=stop,
                                       wait_for_native_ready=True, on_wait=Mock())
