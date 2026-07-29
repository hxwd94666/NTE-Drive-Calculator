# 防止新的大体积二进制与本机运行数据意外进入版本库。
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_TRACKED_FILE_BYTES = 10 * 1024 * 1024
MAX_REVIEWABLE_PYTHON_LINES = 800


class RepositoryHygieneTests(unittest.TestCase):
    def test_no_new_tracked_file_exceeds_size_budget(self):
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            self.skipTest("当前源代码副本不包含可读取的 Git 索引")
        paths = [Path(raw.decode("utf-8")) for raw in result.stdout.split(b"\0") if raw]
        oversized = {
            path.as_posix(): (ROOT / path).stat().st_size
            for path in paths
            if (ROOT / path).is_file() and (ROOT / path).stat().st_size > MAX_TRACKED_FILE_BYTES
        }

        self.assertEqual({}, oversized)

    def test_runtime_and_build_outputs_are_ignored(self):
        ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (
            "/accounts/",
            "/logs/",
            "/scanned_images/",
            "installer/output/",
            "*.sqlite3-wal",
            "*.sqlite3-shm",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, ignore_text)

    def test_python_modules_stay_within_review_threshold(self):
        roots = (ROOT / "src", ROOT / "tools", ROOT / "tests")
        oversized = {
            path.relative_to(ROOT).as_posix(): len(
                path.read_text(encoding="utf-8").splitlines()
            )
            for source_root in roots
            for path in sorted(source_root.rglob("*.py"))
            if len(path.read_text(encoding="utf-8").splitlines())
            > MAX_REVIEWABLE_PYTHON_LINES
        }

        self.assertEqual({}, oversized)


if __name__ == "__main__":
    unittest.main()
