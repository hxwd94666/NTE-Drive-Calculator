# 测试 Windows 窗口捕获诊断。
"""Tests for the read-only Windows capture environment probe."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from src.integrations.windows_capture_diagnostics import collect_windows_capture_support


class WindowsCaptureDiagnosticsTests(unittest.TestCase):
    def test_collects_driver_and_redacted_adapter_facts(self):
        replies = iter(
            [
                subprocess.CompletedProcess(
                    [], 0, '{"Name":"npcap","State":"Running","StartMode":"Auto","Status":"OK"}', ""
                ),
                subprocess.CompletedProcess(
                    [],
                    0,
                    '[{"Name":"Wi-Fi","Status":"Up","InterfaceDescription":"Adapter","HardwareInterface":true}]',
                    "",
                ),
            ]
        )
        with patch("src.integrations.windows_capture_diagnostics.os.name", "nt"):
            result = collect_windows_capture_support(
                core_executable=Path("C:/app/_internal/nte-core.exe"),
                command_runner=lambda _arguments: next(replies),
            )

        self.assertTrue(result["supported"])
        self.assertEqual(result["driver_service"]["details"]["State"], "Running")
        self.assertEqual(result["network_adapters"]["active_count"], 1)
        self.assertEqual(result["network_adapters"]["active_hardware_count"], 1)
        self.assertEqual(result["network_adapters"]["adapters"][0]["name"], "Wi-Fi")
        self.assertNotIn("MacAddress", result["network_adapters"]["adapters"][0])

    def test_preserves_query_failure_without_raising(self):
        failed = subprocess.CompletedProcess([], 1, "", "Get-NetAdapter failed")
        with patch("src.integrations.windows_capture_diagnostics.os.name", "nt"):
            result = collect_windows_capture_support(
                core_executable=Path("C:/app/_internal/nte-core.exe"),
                command_runner=lambda _arguments: failed,
            )

        self.assertEqual(result["driver_service"]["state"], "query_error")
        self.assertEqual(result["network_adapters"]["state"], "query_error")
