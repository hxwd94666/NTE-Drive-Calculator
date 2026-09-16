# 验证原生进度的有界双管道、调用线程交付、取消回收和旧组件兼容。
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from src.domain.native_analysis import BuffProjectionBatchTooLarge
from src.integrations.native_analysis_stream import (
    NativeStreamError, PROGRESS_PHASES, decode_progress,
)
from src.integrations.nte_analysis_core import (
    NativeAnalysisCancelled, NativeAnalysisError, NteAnalysisCoreClient,
)


def event(phase="load", completed=None, total=None):
    return {"kind": "battle_progress_v1", "phase": phase,
            "completed": completed, "total": total}


class ProgressProtocolTests(unittest.TestCase):
    def test_only_fixed_phases_and_exact_nullable_or_integer_counts(self):
        for phase in PROGRESS_PHASES:
            for counts in ((None, None), (0, 0), (1, 2), (2, 2), (2**31 - 1, 2**31 - 1)):
                value = event(phase, *counts)
                self.assertEqual(decode_progress(json.dumps(value).encode()), value)
        for value in (event("private-name"), event(completed=True, total=2),
                      event(completed=1.0, total=2), event(completed=-1, total=2),
                      event(completed=3, total=2), event(completed=None, total=2),
                      event(completed=0, total=2**31), event(completed=10**100, total=10**100),
                      event(completed=1, total=None), {**event(), "payload": "private"}):
            with self.subTest(value=value), self.assertRaises(NativeStreamError):
                decode_progress(json.dumps(value).encode())

    def test_bad_json_large_lines_and_stderr_never_surface_child_text(self):
        duplicate = json.dumps(event()).replace('"phase": "load"', '"phase":"load","phase":"target"').encode()
        for value in (b"private-secret", b"{" * 2200, b"\xff", b"[]", duplicate):
            with self.subTest(length=len(value)), self.assertRaises(NativeStreamError) as error:
                decode_progress(value)
            self.assertNotIn("private-secret", str(error.exception))
        self.assertIsNone(decode_progress(b"nte-analysis-core: invalid_request"))


class ProgressTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "nte-analysis-core.exe"
        path.touch()
        self.client = NteAnalysisCoreClient(path, "fixture", capabilities=frozenset({
            "battle_page_v1", "battle_progress_v1",
        }))
        self.children = []
        self.arguments = []

    def child(self, source):
        popen = subprocess.Popen

        def spawn(args, **kwargs):
            self.arguments.append(args)
            process = popen([sys.executable, "-u", "-c", source], **kwargs)
            self.children.append(process)
            return process

        return patch("src.integrations.nte_analysis_core.subprocess.Popen", side_effect=spawn)

    def assert_clean(self):
        self.assertTrue(self.children)
        for child in self.children:
            self.assertIsNotNone(child.poll())
            self.assertTrue(all(pipe.closed for pipe in (child.stdin, child.stdout, child.stderr)))
        self.assertFalse(any(thread.name.startswith("nte-analysis-")
                             for thread in threading.enumerate()))

    def test_live_events_are_owner_thread_and_dual_pipes_do_not_deadlock(self):
        owner = threading.get_ident()
        events = []
        source = (
            "import sys,json,time\n"
            "sys.stdin.buffer.read()\n"
            "sys.stdout.buffer.write(b'x'*200000);sys.stdout.flush()\n"
            f"sys.stderr.write(json.dumps({event()!r})+'\\n');sys.stderr.flush()\n"
            "time.sleep(.2)\n"
            "sys.stdout.buffer.write(b'y'*200000)\n"
        )

        def receive(value):
            events.append(value)
            self.assertEqual(threading.get_ident(), owner)
            self.assertIsNone(self.children[-1].poll())

        with self.child(source):
            result = self.client._run_progress(b"{}", checkpoint=None, progress_callback=receive)
        self.assertEqual(result, b"x" * 200000 + b"y" * 200000)
        self.assertEqual(events, [event()])
        self.assertIn("--progress", self.arguments[0])
        self.assert_clean()

    def test_cancel_idle_child_checks_at_100ms_and_reaps(self):
        times = []

        def checkpoint():
            times.append(time.monotonic())
            if len(times) >= 4:
                raise NativeAnalysisCancelled("cancel")

        with self.child("import time; time.sleep(30)"):
            with self.assertRaises(NativeAnalysisCancelled):
                self.client._run_progress(b"{}", checkpoint=checkpoint, progress_callback=Mock())
        self.assertLess(times[-1] - times[0], 1.0)
        self.assert_clean()

    def test_large_fast_response_checks_boundaries_not_each_pipe_fragment(self):
        checks = Mock()
        source = "import sys;sys.stdin.buffer.read();sys.stdout.buffer.write(b'x'*2000000)"
        # Hold the clock within one checkpoint period. The number of checks
        # must depend on lifecycle boundaries, not OS pipe fragmentation.
        with self.child(source), patch('src.integrations.native_analysis_stream.time.monotonic', return_value=1.0):
            result = self.client._run_progress(b'{}', checkpoint=checks, progress_callback=Mock())
        self.assertEqual(result, b'x' * 2000000)
        self.assertEqual(checks.call_count, 3)  # Before start, first poll, final delivery.
        self.assert_clean()

    def test_fast_response_still_checks_cancellation_before_delivery(self):
        checks = Mock(side_effect=[None, None, NativeAnalysisCancelled('cancel')])
        with self.child("print('{}')"), patch('src.integrations.native_analysis_stream.time.monotonic', return_value=1.0):
            with self.assertRaises(NativeAnalysisCancelled):
                self.client._run_progress(b'{}', checkpoint=checks, progress_callback=Mock())
        self.assert_clean()

    def test_cancel_blocked_stdin_writer_closes_pipe_and_thread(self):
        checks = []

        def checkpoint():
            checks.append(1)
            if len(checks) >= 4:
                raise NativeAnalysisCancelled("cancel")

        with self.child("import time;time.sleep(30)"):
            with self.assertRaises(NativeAnalysisCancelled):
                self.client._run_progress(b"x" * 2000000, checkpoint=checkpoint,
                                          progress_callback=Mock())
        self.assert_clean()

    def test_stderr_flood_stays_cancellable_with_bounded_queue(self):
        count = []
        source = (
            "import sys,json,time\n"
            f"line=json.dumps({event()!r})+'\\n'\n"
            "for _ in range(100000):\n"
            " sys.stderr.write(line)\n"
            "sys.stderr.flush();time.sleep(30)\n"
        )

        def receive(_value):
            count.append(1)
            if len(count) == 50:
                raise NativeAnalysisCancelled("cancel")

        with self.child(source), self.assertRaises(NativeAnalysisCancelled):
            self.client._run_progress(b"{}", checkpoint=None, progress_callback=receive)
        self.assertEqual(len(count), 50)
        self.assert_clean()

    def test_callback_failure_and_timeout_reap_and_close_threads(self):
        source = (
            "import sys,json,time\n"
            f"sys.stderr.write(json.dumps({event()!r})+'\\n');sys.stderr.flush()\n"
            "time.sleep(30)\n"
        )
        with self.child(source):
            with self.assertRaisesRegex(ValueError, "callback stopped"):
                self.client._run_progress(b"{}", checkpoint=None,
                                          progress_callback=Mock(side_effect=ValueError("callback stopped")))
        self.assert_clean()

    def test_generic_nonzero_exit_does_not_include_stderr(self):
        source = "import sys;sys.stderr.write('nte-analysis-core: rejected\\n');sys.exit(7)"
        with self.child(source), self.assertRaisesRegex(NativeAnalysisError, "退出码 7") as error:
            self.client._run_progress(b"{}", checkpoint=None, progress_callback=Mock())
        self.assertNotIn("rejected", str(error.exception))
        self.assert_clean()
        self.client.timeout = .15
        with self.child("import time;time.sleep(30)"):
            with self.assertRaisesRegex(NativeAnalysisError, "超时"):
                self.client._run_progress(b"{}", checkpoint=None, progress_callback=Mock())
        self.assert_clean()

    def test_stdout_limit_bad_stderr_and_unterminated_events_reap(self):
        cases = (
            ("import sys;sys.stdout.buffer.write(b'x'*20000)", "大小限制"),
            ("import sys;sys.stderr.buffer.write(b'x'*3000)", "大小限制"),
            ("import sys;sys.stderr.write('private-secret\\n')", "协议无效"),
            (f"import sys,json;sys.stderr.write(json.dumps({event()!r}))", "协议无效"),
        )
        for source, expected in cases:
            with self.subTest(expected=expected), self.child(source):
                with patch("src.integrations.nte_analysis_core.MAX_BYTES", 10000):
                    with self.assertRaisesRegex(NativeAnalysisError, expected) as error:
                        self.client._run_progress(b"{}", checkpoint=None, progress_callback=Mock())
            self.assertNotIn("private-secret", str(error.exception))
            self.assert_clean()

    def test_common_nonzero_error_semantics_and_fixed_stderr_discard(self):
        response = {"schema_version": "nte-analysis-response-v1", "engine_version": "0.3.0",
                    "error": {"code": "projection_plan_too_large"}}
        source = ("import sys,json;sys.stdin.buffer.read();"
                  f"print(json.dumps({response!r}));"
                  "sys.stderr.write('nte-analysis-core: projection_plan_too_large\\n');sys.exit(2)")
        with self.child(source), self.assertRaises(BuffProjectionBatchTooLarge):
            self.client._run_progress(b"{}", checkpoint=None, progress_callback=Mock())
        self.assert_clean()

    def test_legacy_and_no_callback_use_unchanged_nonstream_run(self):
        request = {"account_id": "fixture", "generation": 8, "battle_record_id": 7}
        response = {"schema_version": "nte-analysis-response-v1", "engine_version": "0.3.0",
                    "batch_kind": "battle_page_v1", "dataset_version": "fixture", **request,
                    "compute_elapsed_ns": 0, "result": {"ok": True}}
        encoded = json.dumps(response).encode()
        for capable, callback in ((True, None), (False, Mock()), (True, Mock())):
            self.client.supports_battle_progress = capable
            with patch.object(self.client, "_run", return_value=encoded) as plain:
                with patch.object(self.client, "_run_progress", return_value=encoded) as stream:
                    self.assertEqual(self.client.load_battle_page(
                        request, progress_callback=callback), {"ok": True})
            self.assertEqual(stream.call_count, int(capable and callback is not None))
            self.assertEqual(plain.call_count, int(not capable or callback is None))

    def test_version_rejects_missing_advertised_progress_capability(self):
        identity = {"engine": "nte-analysis-core", "engine_version": "0.3.0",
                    "capabilities": ["battle_page_v1"]}
        with patch.object(self.client, "_run", return_value=json.dumps(identity).encode()):
            with self.assertRaisesRegex(NativeAnalysisError, "进度能力"):
                self.client.version()

    def test_progress_is_local_to_each_request(self):
        callbacks = [Mock(), Mock()]
        for index, callback in enumerate(callbacks):
            value = event("core_candidates", index, 2)
            source = f"import sys,json;sys.stdin.buffer.read();sys.stderr.write(json.dumps({value!r})+'\\n');print('{{}}')"
            with self.child(source):
                self.client._run_progress(b"{}", checkpoint=None, progress_callback=callback)
            callback.assert_called_once_with(value)
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
