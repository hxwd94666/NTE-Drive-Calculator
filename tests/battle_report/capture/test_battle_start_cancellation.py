# 验证启动各阶段取消正常退场，已开始采集后的真实错误仍然可见。
from concurrent.futures import CancelledError
from unittest.mock import Mock

import pytest

from src.observability import OperationContext
from src.services.battle_report.capture.service import BattleCaptureService
from tests.battle_report.capture.test_battle_native_capture_lifecycle import _NativeCore
from tests.battle_report.capture.test_battle_capture_axis_service import _Writer
from tests.battle_report.capture.test_battle_capture_axis_service import _wait_until


@pytest.mark.parametrize('stage', ['factory', 'start', 'add_event_handler', 'start_capture'])
@pytest.mark.parametrize('error_type', [CancelledError, RuntimeError])
def test_startup_cancellation_releases_owner_without_false_failure(stage, error_type):
    core = _NativeCore(auto_end=False)
    core.close = Mock()
    writer = _Writer()

    def interrupted(*args, **kwargs):
        # UI 取消意图可先于服务 stop_event；异常类型是已撤销启动的明确信号。
        raise error_type('本次战报启动已取消。' if error_type is CancelledError else '连接失败')

    factory = interrupted if stage == 'factory' else lambda: core
    if stage != 'factory':
        setattr(core, stage, interrupted)
    service = BattleCaptureService(
        client_factory=factory, summary_writer=writer, required_source='native',
        operation_guard=lambda _: None, operation_context=OperationContext.create('battle_report'),
    )
    try:
        service.start()
        assert _wait_until(lambda: not service.is_running)
        if error_type is CancelledError:
            assert service.state.phase == 'stopped', service.state.error
            assert service.state.persistence_status == 'skipped_empty'
            assert '启动已取消' in service.state.message
            assert not service.state.error
        else:
            assert service.state.phase == 'error'
            assert '连接失败' in service.state.error
        assert not core.capture_started.is_set()
        assert not core.axis_requests and not core.record_requests
        assert writer.record is None
        assert writer.discarded == (stage != 'factory')
        assert core.close.call_count == (0 if stage == 'factory' else 1)
    finally:
        service.close(timeout=2)


def test_cancellation_after_capture_started_is_not_misclassified_as_empty_start():
    core = _NativeCore()
    writer = _Writer()

    def interrupted(**kwargs):
        raise CancelledError('最终战报读取被取消')

    core.get_battle_record = interrupted
    service = BattleCaptureService(
        client_factory=lambda: core, summary_writer=writer, required_source='native',
        operation_guard=lambda _: None, operation_context=OperationContext.create('battle_report'),
    )
    try:
        service.start()
        assert _wait_until(lambda: not service.is_running)
        assert core.capture_started.is_set()
        assert service.state.phase == 'error'
        assert '最终战报读取被取消' in service.state.error
        assert writer.record is None and writer.discarded
    finally:
        service.close(timeout=2)
