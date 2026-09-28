# 验证扫描工作线程在失败路径也会释放扫描器持有的资源。
"""ScanWorkerThread 必须在异常时释放 scanner，避免虚拟手柄与句柄泄漏。

根因（2026-09-28 定位）：`ScanWorkerThread.run` 原先没有 `finally`，异常或取消
时不会调用 `scanner.close()`；同文件的 `FullVisualScanParseWorkerThread` 则有
释放逻辑，两者不一致。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.app.workers import ScanWorkerThread


class _FakeScanner:
    def __init__(self, **_kwargs) -> None:
        self.closed = False

    def start_semi_auto_scan(self) -> int:
        raise RuntimeError("半自动扫描失败")

    def close(self) -> None:
        self.closed = True


class ScanWorkerLifecycleTests(unittest.TestCase):
    def test_failed_semi_auto_scan_releases_the_scanner(self) -> None:
        created: list[_FakeScanner] = []

        def factory(**kwargs) -> _FakeScanner:
            scanner = _FakeScanner(**kwargs)
            created.append(scanner)
            return scanner

        worker = ScanWorkerThread(
            output_dir="out", template_path="tpl", mode="semi",
        )
        with patch("src.app.workers.DroneScanner", factory):
            worker.run()

        self.assertEqual(1, len(created))
        self.assertTrue(created[0].closed, "扫描失败后必须释放扫描器资源")


if __name__ == "__main__":
    unittest.main()
