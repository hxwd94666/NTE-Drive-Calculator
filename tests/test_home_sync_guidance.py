# 测试工作台背包同步错误到用户处理建议的映射。
from __future__ import annotations

import unittest

from src.features.home.page import inventory_sync_error_guidance


class HomeSyncGuidanceTests(unittest.TestCase):
    def test_npcap_error_points_to_environment_configuration(self) -> None:
        guidance = inventory_sync_error_guidance(
            "NPCAP_NOT_FOUND",
            "NteCoreRpcError: [NPCAP_NOT_FOUND]",
        )

        self.assertIn("环境配置", guidance)
        self.assertIn("Npcap 1.88", guidance)

    def test_capture_device_error_explains_how_to_restore_auto_selection(self) -> None:
        guidance = inventory_sync_error_guidance("CAPTURE_DEVICE_NOT_FOUND", "")

        self.assertIn("清空抓取网卡", guidance)
        self.assertIn("自动选择", guidance)

    def test_unknown_error_retains_a_general_recovery_path(self) -> None:
        guidance = inventory_sync_error_guidance("UNKNOWN", "something failed")

        self.assertIn("重新启动同步", guidance)
        self.assertIn("日志", guidance)
