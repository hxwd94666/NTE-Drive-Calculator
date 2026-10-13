# 测试账号级 nte-core pcapng 抓包保留与清理策略。
"""Regression tests for per-account nte-core .pcapng retention."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.integrations.capture.raw_retention import prune_raw_capture_files


class RawCaptureRetentionTests(unittest.TestCase):
    def _write(self, root: Path, name: str, size: int, modified_at: int) -> Path:
        path = root / name
        path.write_bytes(b"x" * size)
        os.utime(path, ns=(modified_at, modified_at))
        return path

    def test_prunes_old_pcapng_but_not_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "capture"
            root.mkdir()
            sibling = Path(temporary) / "other_capture"
            sibling.mkdir()
            other_capture = self._write(sibling, "other.pcapng", 8, 1)
            nested = root / "nested"
            nested.mkdir()
            nested_capture = self._write(nested, "nested.pcapng", 8, 1)
            capture_directory = root / "directory.pcapng"
            capture_directory.mkdir()
            now = time.time_ns()
            oldest = self._write(root, "oldest.pcapng", 8, now - 3_000_000_000)
            middle = self._write(root, "middle.PCAPNG", 8, now - 2_000_000_000)
            newest = self._write(root, "newest.pcapng", 8, now - 1_000_000_000)
            note = self._write(root, "nte-core.log", 8, now)

            result = prune_raw_capture_files(root, retain_count=2, max_total_bytes=32)

            self.assertFalse(oldest.exists())
            self.assertTrue(middle.exists())
            self.assertTrue(newest.exists())
            self.assertTrue(note.exists())
            self.assertTrue(other_capture.exists())
            self.assertTrue(nested_capture.exists())
            self.assertTrue(capture_directory.is_dir())
            self.assertEqual(1, result.deleted_count)
            self.assertEqual(8, result.deleted_bytes)
            self.assertEqual(2, result.retained_count)
            self.assertEqual(16, result.retained_bytes)

    def test_keeps_newest_capture_when_it_exceeds_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = time.time_ns()
            old = self._write(root, "old.pcapng", 8, now - 2_000_000_000)
            newest = self._write(root, "latest.pcapng", 64, now - 1_000_000_000)

            result = prune_raw_capture_files(root, retain_count=5, max_total_bytes=16)

            self.assertFalse(old.exists())
            self.assertTrue(newest.exists())
            self.assertEqual(1, result.retained_count)


    def test_unlink_failure_retains_file_until_next_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = time.time_ns()
            oldest = self._write(root, "oldest.pcapng", 8, now - 3_000_000_000)
            middle = self._write(root, "middle.pcapng", 8, now - 2_000_000_000)
            newest = self._write(root, "newest.pcapng", 8, now - 1_000_000_000)
            unlink = Path.unlink

            def fail_one(path: Path) -> None:
                if path == oldest:
                    raise PermissionError("synthetic capture still flushing")
                unlink(path)

            # 失败文件仍计入实际保留量，后续一次成功清理才删除它。
            with patch.object(Path, "unlink", autospec=True, side_effect=fail_one):
                result = prune_raw_capture_files(root, retain_count=1, max_total_bytes=16)
            self.assertTrue(oldest.exists())
            self.assertFalse(middle.exists())
            self.assertTrue(newest.exists())
            self.assertEqual((1, 8, 2, 16), (
                result.deleted_count, result.deleted_bytes, result.retained_count, result.retained_bytes,
            ))
            result = prune_raw_capture_files(root, retain_count=1, max_total_bytes=16)
            self.assertFalse(oldest.exists())
            self.assertTrue(newest.exists())
            self.assertEqual((1, 8, 1, 8), (
                result.deleted_count, result.deleted_bytes, result.retained_count, result.retained_bytes,
            ))


if __name__ == "__main__":
    unittest.main()
