# 校验流水线扫描在落盘前复核账号代次，过期结果不得提交副作用。
"""过期扫描结果不得搬移截图：提交前必须复核 result_is_current。

根因（2026-09-28 定位）：`run_streaming_scan_parse` 原先只在解析前与提交后复核
代次；提交本身会真实搬移截图并删除临时目录，代次在解析期间推进时无法回收。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.services.streaming_scan_service import run_streaming_scan_parse


class _FakeScanner:
    def __init__(self, root: Path) -> None:
        self.output_dir = str(root)
        self.temp_dir = root / "temp"
        self.temp_dir.mkdir()
        self.committed = False

    def start_scan(self, total_drives, on_capture=None, commit_on_complete=True):
        self.commit_on_complete = commit_on_complete
        for index in range(1, total_drives + 1):
            path = self.temp_dir / f"raw_drive_{index:04d}.png"
            path.write_bytes(b"png")
            on_capture(str(path), index, total_drives)
        return total_drives

    def _commit_temp_output(self) -> None:
        self.committed = True


class _FakeProcessor:
    def __init__(self) -> None:
        self.inventory: list[dict] = []
        self.exported = False

    def process_image_file(self, image_path, filename, **_kwargs):
        self.inventory.append({"filename": filename})
        return SimpleNamespace(item_type="drive"), True

    def _export_to_json(self) -> None:
        self.exported = True


class StreamingScanCommitBoundaryTests(unittest.TestCase):
    def test_stale_generation_skips_commit_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scanner = _FakeScanner(Path(temporary))
            processor = _FakeProcessor()
            calls = {"count": 0}

            def result_is_current() -> bool:
                calls["count"] += 1
                # 解析前复核通过，解析完成时代次已经推进。
                return calls["count"] == 1

            stats = run_streaming_scan_parse(
                scanner,
                processor,
                total_drives=2,
                parse_during_scan=True,
                result_is_current=result_is_current,
                allow_post_actions=False,
            )

        self.assertFalse(
            scanner.committed,
            "代次已推进的扫描结果不得提交截图与临时目录改动",
        )
        self.assertTrue(stats["discarded_stale"])


if __name__ == "__main__":
    unittest.main()
