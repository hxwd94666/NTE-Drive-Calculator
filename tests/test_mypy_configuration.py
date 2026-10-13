# 检查渐进类型清单非空、去重及现存 Python 路径，避免质量入口空跑或引用退役模块。
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MypyConfigurationTests(unittest.TestCase):

    def test_allowlist_contains_only_existing_python_modules(self):
        allowlist = ROOT / "tools/quality/mypy_allowlist.txt"
        paths = [
            line.strip()
            for line in allowlist.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertTrue(paths)
        self.assertEqual(len(paths), len(set(paths)))
        missing = [
            relative_path
            for relative_path in paths
            if not (ROOT / relative_path).is_file()
            or not relative_path.endswith(".py")
        ]
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
