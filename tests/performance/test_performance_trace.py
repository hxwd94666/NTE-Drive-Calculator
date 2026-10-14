# 验证细分性能采样的停止确认、账号隔离、独立生命周期与缺失值契约。
import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

from src.integrations.performance.journals import validate_trace, PHASES
from src.services.performance.trace import PerformanceTrace
from src.integrations.nte_core_protocol import NteCoreRpcError

NTE_TEST_TIER = 'core'


def receipt(trace_id, active=True):
    return dict(trace_id=trace_id, active=active, stopping=False, failed=False,
                complete=not active, written=0, dropped=0, path='', stop_reason='requested', summaries=[])


def wait_for(predicate):
    end = time.monotonic() + 4
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('timed out')


class TraceTests(unittest.TestCase):
    def test_failed_journal_start_closes_owned_stream(self):
        from unittest.mock import Mock, patch
        from src.integrations.performance.journals import TraceJournal
        from src.integrations.performance.journals import PerformanceJournal

        with tempfile.TemporaryDirectory() as temporary:
            for journal_type in (TraceJournal, PerformanceJournal):
                for failing_step in ('write', 'flush'):
                    with self.subTest(journal=journal_type.__name__, step=failing_step):
                        stream = Mock()
                        getattr(stream, failing_step).side_effect = OSError('disk unavailable')
                        arguments = (Path(temporary), 'trace-id', 1) if journal_type is TraceJournal else (Path(temporary),)
                        # 构造未返回时后台owner拿不到journal，已打开流必须由构造者释放。
                        with patch.object(Path, 'open', return_value=stream):
                            with self.assertRaisesRegex(OSError, 'disk unavailable'):
                                journal_type(*arguments)
                        stream.close.assert_called_once()

    def test_stop_waits_for_drained_receipt_and_saves_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            def control(payload, key):
                calls.append(payload['action'])
                return receipt(payload['trace_id'], payload['action'] != 'status' or 'stop' not in calls)
            service = PerformanceTrace(control)
            service.configure(('one', 1), True, Path(temporary))
            service.start(1)
            wait_for(lambda: service.snapshot()['state'] == 'collecting')
            service.stop()
            self.assertTrue(service.wait())
            self.assertEqual(calls.count('start'), 1)
            self.assertEqual(calls[-2:], ['stop', 'status'])
            self.assertEqual(service.snapshot()['state'], 'saved')
            rows = [json.loads(line) for line in Path(service.snapshot()['log_path']).read_text(encoding='utf-8').splitlines()]
            self.assertFalse(rows[-1]['active'])
            self.assertTrue(rows[-1]['complete'])

    def test_account_change_drops_old_reply_and_does_not_write_new_account(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entered, release = Event(), Event()
            seen = []
            def control(payload, key):
                seen.append(key)
                if payload['action'] == 'start':
                    entered.set()
                    release.wait(2)
                return receipt(payload['trace_id'], False)
            service = PerformanceTrace(control)
            service.configure(('one', 1), True, root / 'old')
            service.start(1)
            self.assertTrue(entered.wait(2))
            service.configure(('two', 2), True, root / 'new')
            release.set()
            self.assertTrue(service.wait())
            self.assertEqual(service.snapshot()['state'], 'off')
            self.assertFalse((root / 'new').exists())
            self.assertTrue(all(key == ('one', 1) for key in seen))

    def test_revocation_stops_independent_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            def control(payload, key):
                calls.append(payload['action'])
                return receipt(payload['trace_id'], payload['action'] == 'start')
            service = PerformanceTrace(control)
            directory = Path(temporary)
            service.configure('one', True, directory)
            service.start(1)
            wait_for(lambda: service.snapshot()['state'] == 'collecting')
            service.configure('one', False, directory)
            self.assertTrue(service.wait())
            self.assertIn('stop', calls)
            with self.assertRaises(PermissionError):
                service.start(1)

    def test_unknown_start_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            def control(payload, key):
                calls.append(payload['action'])
                if payload['action'] == 'start':
                    raise TimeoutError('unknown')
                return receipt(payload['trace_id'], False)
            service = PerformanceTrace(control)
            service.configure('one', True, Path(temporary))
            service.start(1)
            self.assertTrue(service.wait())
            self.assertEqual(calls, ['start', 'stop'])

    def test_unknown_phase_is_not_zero_and_identity_is_validated(self):
        value = receipt('id')
        phases = {p: dict(n=0, p95=None, p99=None, max=None) for p in PHASES}
        value['summaries'] = [dict(source='hud_frame', phases_us=phases)]
        validate_trace(value, 'id')
        with self.assertRaises(ValueError):
            validate_trace(value, 'other')
        phases['team']['max'] = 0
        with self.assertRaises(ValueError):
            validate_trace(value, 'id')

    def test_rejected_start_preserves_reason_and_does_not_stop_another_trace(self):
        with tempfile.TemporaryDirectory() as temporary:
            calls = []
            def control(payload, key):
                calls.append(payload['action'])
                raise NteCoreRpcError({'code': -32001, 'message': 'performance_busy'})
            service = PerformanceTrace(control)
            service.configure('one', True, Path(temporary))
            service.start(1)
            self.assertTrue(service.wait())
            self.assertEqual(calls, ['start'])
            self.assertEqual(service.snapshot()['state'], 'failed')
            self.assertIn('performance_busy', service.snapshot()['error'])

    def test_start_and_stop_failures_preserve_both_causes(self):
        with tempfile.TemporaryDirectory() as temporary:
            def control(payload, key):
                raise RuntimeError('start connection lost' if payload['action'] == 'start' else 'stop connection changed')
            service = PerformanceTrace(control)
            service.configure('one', True, Path(temporary))
            service.start(1)
            self.assertTrue(service.wait())
            value = service.snapshot()
            self.assertEqual(value['state'], 'unconfirmed')
            self.assertIn('start connection lost', value['error'])
            self.assertIn('stop connection changed', value['error'])
            self.assertTrue(Path(value['log_path']).is_file())


if __name__ == '__main__':
    unittest.main()
