"""Regression tests for per-account nte-core .pcapng retention."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from src.services.raw_capture_retention import prune_raw_capture_files


class RawCaptureRetentionTests(unittest.TestCase):
    def _write(self, root: Path, name: str, size: int, modified_at: int) -> Path:
        path = root / name
        path.write_bytes(b"x" * size)
        os.utime(path, ns=(modified_at, modified_at))
        return path

    def test_prunes_old_pcapng_but_not_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            now = time.time_ns()
            oldest = self._write(root, "oldest.pcapng", 8, now - 3_000_000_000)
            middle = self._write(root, "middle.pcapng", 8, now - 2_000_000_000)
            newest = self._write(root, "newest.pcapng", 8, now - 1_000_000_000)
            note = self._write(root, "nte-core.log", 8, now)

            result = prune_raw_capture_files(root, retain_count=2, max_total_bytes=32)

            self.assertFalse(oldest.exists())
            self.assertTrue(middle.exists())
            self.assertTrue(newest.exists())
            self.assertTrue(note.exists())
            self.assertEqual(1, result.deleted_count)
            self.assertEqual(2, result.retained_count)

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


if __name__ == "__main__":
    unittest.main()
