from __future__ import annotations

import unittest
from pathlib import Path

from src.integrations.nte_core import NteCoreRpcError
from src.services.nte_core_diagnostics import (
    capture_device_names,
    collect_nte_core_diagnostics,
    format_nte_core_diagnostics,
)


class _FakeClient:
    def __init__(self, _executable: Path, *, failed: bool = False) -> None:
        self.failed = failed
        self.hello_result = {
            "core_version": "0.3.5",
            "protocol_version": 1,
            "data_version": "1",
        }
        self.recent_stderr = ("core diagnostic stderr",)
        self.closed = False

    def start(self):
        return self

    def detect_capture_environment(self):
        if self.failed:
            raise NteCoreRpcError(
                {
                    "code": -32000,
                    "message": "game probe failed",
                    "data": {"domain_code": "GAME_PROCESS_NOT_FOUND"},
                }
            )
        return {
            "game_process_detected": False,
            "local_ip_detected": False,
            "recommended_device": None,
            "devices": [{"name": "adapter-1"}],
        }

    def close(self) -> None:
        self.closed = True


class NteCoreDiagnosticsTests(unittest.TestCase):
    def test_collects_capture_detection_without_starting_capture(self):
        client = _FakeClient(Path("fake.exe"))
        result = collect_nte_core_diagnostics(
            client_factory=lambda _executable: client,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["hello"]["core_version"], "0.3.5")
        self.assertFalse(result["capture_detect"]["game_process_detected"])
        self.assertTrue(client.closed)

    def test_lists_available_devices_for_manual_capture_selection(self):
        result = {
            "executable": "fake.exe",
            "capture_detect": {
                "recommended_device": None,
                "devices": [
                    {"name": "\\\\Device\\\\NPF_{one}"},
                    {"name": "\\\\Device\\\\NPF_{two}"},
                    {"name": "\\\\Device\\\\NPF_{one}"},
                    {"description": "missing name"},
                ],
            },
        }

        self.assertEqual(
            capture_device_names(result["capture_detect"]),
            ["\\\\Device\\\\NPF_{one}", "\\\\Device\\\\NPF_{two}"],
        )
        report = format_nte_core_diagnostics(result)
        self.assertIn("可手动填写的抓取网卡", report)
        self.assertIn("未获得自动推荐", report)
        self.assertIn("\\\\Device\\\\NPF_{one}", report)

    def test_preserves_core_domain_error_in_copyable_report(self):
        client = _FakeClient(Path("fake.exe"), failed=True)
        result = collect_nte_core_diagnostics(
            client_factory=lambda _executable: client,
        )

        report = format_nte_core_diagnostics(result)
        self.assertFalse(result["ok"])
        self.assertEqual(result["domain_code"], "GAME_PROCESS_NOT_FOUND")
        self.assertIn("GAME_PROCESS_NOT_FOUND", report)
        self.assertIn("core diagnostic stderr", report)
