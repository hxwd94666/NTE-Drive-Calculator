# 验证 nte-core 客户端的并发请求、事件、错误和进程生命周期。
import os
import sys
import tempfile
import textwrap
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from src.integrations.nte_core import (
    NteCoreClient,
    NteCoreRpcError,
    NteCoreTimeoutError,
    resolve_nte_core_executable,
)


FAKE_SIDECAR = r'''
import json
import sys

print("fake sidecar ready", file=sys.stderr, flush=True)
status_sequence = 0
for line in sys.stdin:
    request = json.loads(line)
    request_id = request["id"]
    method = request["method"]
    if method == "core.hello":
        result = {
            "core_version": "test",
            "protocol_version": 1,
            "data_version": "1",
            "capabilities": ["capture", "inventory", "battle_summary"],
            "raw_capture_default": True,
        }
    elif method == "core.status":
        status_sequence += 1
        result = {"core_state": "idle", "status_sequence": status_sequence}
    elif method == "capture.detect":
        result = {
            "game_process_detected": False,
            "recommended_device": None,
            "local_ip_detected": False,
            "devices": [],
        }
    elif method == "capture.start":
        print(json.dumps({
            "jsonrpc": "2.0",
            "method": "event.capture.status",
            "params": {
                "sequence": 1,
                "operation_id": "capture-1",
                "status": "running",
                "profile": request["params"]["profile"],
            },
        }), flush=True)
        result = {"operation_id": "capture-1", "received": request["params"]}
    elif method == "capture.stop":
        result = {"operation_id": "capture-1", "stopped": True}
    elif method == "inventory.get_latest":
        result = {
            "generation": 7,
            "complete": True,
            "item_count": 1,
            "items": [{"item_id": "raw-item", "future_field": {"kept": True}}],
        }
    elif method == "battle.get_summary":
        print(json.dumps({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32000,
                "message": "Core error",
                "data": {"domain_code": "BATTLE_NOT_READY"},
            },
        }), flush=True)
        continue
    elif method == "test.never":
        continue
    elif method == "core.shutdown":
        result = {"shutting_down": True}
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
        break
    else:
        result = {"method": method, "params": request.get("params")}
    print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}), flush=True)
'''


class NteCoreClientTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.sidecar = self.root / "fake_sidecar.py"
        self.sidecar.write_text(textwrap.dedent(FAKE_SIDECAR), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_client(self, **options):
        return NteCoreClient(
            command=[sys.executable, str(self.sidecar)],
            timeout=2.0,
            **options,
        )

    def test_concurrent_calls_events_raw_dtos_and_graceful_shutdown(self):
        callback_received = threading.Event()
        callback_events = []
        stderr_lines = []
        client = self.create_client(stderr_handler=stderr_lines.append)
        client.add_event_handler(
            "event.capture.status",
            lambda event: (callback_events.append(event), callback_received.set()),
        )

        client.start()
        self.assertEqual(client.hello_result["protocol_version"], 1)
        with ThreadPoolExecutor(max_workers=6) as executor:
            statuses = list(executor.map(lambda _: client.status(), range(12)))
        self.assertEqual(
            sorted(status["status_sequence"] for status in statuses),
            list(range(1, 13)),
        )

        started = client.start_capture(profile="inventory", raw_capture="disabled")
        self.assertEqual(started["operation_id"], "capture-1")
        self.assertEqual(started["received"]["device"], {"mode": "auto"})
        self.assertEqual(started["received"]["raw_capture"], "disabled")
        event = client.get_event(timeout=1.0)
        self.assertEqual(event["method"], "event.capture.status")
        self.assertTrue(callback_received.wait(timeout=1.0))
        self.assertEqual(callback_events[0], event)

        inventory = client.get_latest_inventory()
        self.assertEqual(inventory["items"][0]["future_field"], {"kept": True})
        self.assertEqual(client.stop_capture()["stopped"], True)

        with self.assertRaises(NteCoreRpcError) as raised:
            client.get_battle_summary()
        self.assertEqual(raised.exception.code, -32000)
        self.assertEqual(raised.exception.domain_code, "BATTLE_NOT_READY")

        self.assertEqual(client.shutdown(), {"shutting_down": True})
        self.assertFalse(client.is_running)
        client.close()
        self.assertIn("fake sidecar ready", stderr_lines)

    def test_timeout_does_not_break_following_requests(self):
        with self.create_client() as client:
            with self.assertRaises(NteCoreTimeoutError):
                client.call("test.never", timeout=0.05)
            self.assertEqual(client.status()["core_state"], "idle")

    def test_environment_override_resolves_executable(self):
        executable = self.root / "nte-core.exe"
        executable.write_bytes(b"test")
        with patch.dict(os.environ, {"NTE_CORE_EXE": str(executable)}):
            self.assertEqual(resolve_nte_core_executable(), executable.resolve())

    def test_frozen_bundle_resolves_internal_sidecar(self):
        executable = self.root / "nte_core" / "nte-core.exe"
        executable.parent.mkdir()
        executable.write_bytes(b"test")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(sys, "_MEIPASS", str(self.root), create=True),
        ):
            self.assertEqual(resolve_nte_core_executable(), executable.resolve())

    def test_facade_creates_unstarted_client_with_account_log_directory(self):
        try:
            from src.app import runtime
            from src.app.facade import NTEAppFacade
        except ModuleNotFoundError as exc:
            self.skipTest(f"optional project dependencies are not installed: {exc}")
        with (
            patch.object(runtime, "LOG_DIR", self.root / "logs"),
            patch.object(runtime, "APP_DIR", self.root),
        ):
            facade = NTEAppFacade(config_dir=self.root, user_config_dir=self.root)
            client = facade.create_nte_core_client(
                command=[sys.executable, str(self.sidecar)]
            )
        self.assertFalse(client.is_running)
        self.assertEqual(client.data_dir, (self.root / "logs" / "nte_core").resolve())


if __name__ == "__main__":
    unittest.main()
