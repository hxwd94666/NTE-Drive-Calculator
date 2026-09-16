# 验证实时查询延迟不结束战报，真实停止和最终保存失败仍明确上报。
from __future__ import annotations

import threading

import pytest

from src.integrations.nte_core_protocol import NteCoreProcessError, NteCoreTimeoutError
from src.observability import OperationContext
from src.services.battle_capture_polling import poll_battle_until_stopped
from src.services.battle_capture_service import BattleCaptureService
from tests.test_battle_capture_axis_service import _Core, _Writer, _wait_until


def service_for(core):
    writer = _Writer()
    service = BattleCaptureService(client_factory=lambda: core, summary_writer=writer,
        operation_guard=lambda _: None, required_source='packet',
        operation_context=OperationContext.create('battle_report'))
    return service, writer


def test_record_and_axis_timeout_recover_in_the_same_capture_and_save():
    class Delayed(_Core):
        def __init__(self):
            super().__init__()
            self.record_timeout = self.axis_timeout = True

        def get_battle_record(self, **kwargs):
            if self.record_timeout:
                self.record_timeout = False
                raise NteCoreTimeoutError('battle.get_record', 10)
            return super().get_battle_record(**kwargs)

        def get_battle_axis(self, **kwargs):
            if self.axis_timeout:
                self.axis_timeout = False
                raise NteCoreTimeoutError('battle.get_axis', 10)
            return super().get_battle_axis(**kwargs)

    core = Delayed()
    service, writer = service_for(core)
    states = []
    service.add_state_handler(states.append)
    service.start()
    try:
        assert _wait_until(lambda: bool(writer.pages), timeout=4)
        assert service.is_running and not core.finalized and not writer.discarded
        assert any('查询延迟' in state.message for state in states)
        assert any('查询已恢复' in state.message for state in states)
    finally:
        service.request_stop()
        service.close(timeout=3)
    assert service.state.persistence_status == 'saved'
    assert writer.record is not None


def test_empty_capture_and_repeated_timeouts_have_no_idle_deadline():
    class VirtualStop:
        ticks = 0

        def wait(self, _seconds):
            self.ticks += 1
            return self.ticks > 200

        def is_set(self):
            return self.ticks > 200

    stop = VirtualStop()
    polls = 0
    messages = []

    def poll():
        nonlocal polls
        polls += 1
        if polls % 2:
            raise NteCoreTimeoutError('battle.get_record', 10)
        return None

    poll_battle_until_stopped(poll, stop_event=stop,
        operation=OperationContext.create('battle_report'), notify=messages.append)
    assert polls == 200


def test_process_failure_is_not_swallowed_as_query_delay():
    class Dead(_Core):
        def get_battle_record(self, **kwargs):
            raise NteCoreProcessError('synthetic process closed')

    service, writer = service_for(Dead())
    service.start()
    assert _wait_until(lambda: not service.is_running)
    assert service.state.phase == 'error' and writer.discarded


@pytest.mark.parametrize('status', ['failed', 'stopped'])
def test_packet_terminal_event_ends_live_capture(status):
    class Ended(_Core):
        def add_event_handler(self, method, handler):
            if method == 'event.capture.status':
                self.status_handler = handler

        def remove_event_handler(self, method, handler):
            pass

    core = Ended()
    service, writer = service_for(core)
    service.start()
    assert core.capture_started.wait(1)
    core.status_handler({'params': {'profile': 'combat', 'operation_id': 'capture-1', 'status': status}})
    assert _wait_until(lambda: not service.is_running)
    assert service.state.phase == 'error' and writer.discarded


def test_final_record_timeout_still_prevents_false_success():
    class FinalDelayed(_Core):
        def get_battle_record(self, **kwargs):
            if self.finalized:
                raise NteCoreTimeoutError('battle.get_record', 10)
            return super().get_battle_record(**kwargs)

    service, writer = service_for(FinalDelayed())
    service.start()
    assert _wait_until(lambda: bool(writer.pages))
    service.request_stop()
    service.close(timeout=3)
    assert service.state.phase == 'error'
    assert writer.discarded and writer.record is None


def test_non_battle_timeout_is_not_retried():
    def poll():
        raise NteCoreTimeoutError('equipment.execute', 10)

    with pytest.raises(NteCoreTimeoutError):
        poll_battle_until_stopped(poll, stop_event=threading.Event(),
            operation=OperationContext.create('battle_report'), notify=lambda _: None)
