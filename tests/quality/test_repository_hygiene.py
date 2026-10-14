# 防止新的大体积二进制与本机运行数据意外进入版本库。
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAX_TRACKED_FILE_BYTES = 10 * 1024 * 1024
TRACKED_FILE_SIZE_BUDGETS = {
    # 规范化蓝图和 Buff 证据属于发行输入；体积仍有上界，防止误收原始包。
    "data/game_static.sqlite3": 96 * 1024 * 1024,
    # 正式角色图鉴只承载已晋升的规范化资料及其来源清单。
    "data/role_catalog/game_static.sqlite3": 36 * 1024 * 1024,
    # 独立分析组件可随版本管理；这里仅检查体积，身份由 component.json 哈希核对。
    "third_party/analysis-core/bin/nte-analysis-core.exe": 24 * 1024 * 1024,
}
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
            if (
                (ROOT / path).is_file()
                and (ROOT / path).stat().st_size
                > TRACKED_FILE_SIZE_BUDGETS.get(
                    path.as_posix(),
                    MAX_TRACKED_FILE_BYTES,
                )
            )
        }

        self.assertEqual({}, oversized)

    def test_runtime_and_build_outputs_are_ignored(self):
        if not (ROOT / ".git").exists():
            self.skipTest("导出的源码副本没有 Git 元数据；忽略规则在仓库中验证")
        command = ["git", "--no-optional-locks", "-c", f"safe.directory={ROOT.as_posix()}"]
        runtime_paths = {
            "accounts/fixture/user_data.sqlite3", "logs/fixture.log",
            "scanned_images/fixture.png", "installer/output/fixture.exe",
            "build/fixture.py", "fixture.sqlite3-wal", "fixture.sqlite3-shm",
        }
        # 核对 Git 实际行为，不因注释里仍留有模式文字而误判规则有效。
        ignored = subprocess.run(
            [*command, "check-ignore", "--no-index", "-z", "--stdin"],
            cwd=ROOT, input=("\0".join(sorted(runtime_paths)) + "\0").encode("utf-8"),
            capture_output=True, check=False,
        )
        self.assertEqual(0, ignored.returncode, ignored.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(runtime_paths, {raw.decode("utf-8") for raw in ignored.stdout.split(b"\0") if raw})

        # 同时看已跟踪和磁盘上新增的源码；宽泛 build/ 曾使完整业务包在迁移时消失。
        sources = subprocess.run(
            [*command, "ls-files", "--cached", "--others", "--ignored", "--exclude-standard",
             "-z", "--", "src", "tools", "tests"],
            cwd=ROOT, capture_output=True, check=False,
        )
        self.assertEqual(0, sources.returncode, sources.stderr.decode("utf-8", errors="replace"))
        hidden_python = {
            path for raw in sources.stdout.split(b"\0")
            if raw and (path := raw.decode("utf-8")).endswith(".py") and (ROOT / path).is_file()
        }
        self.assertEqual(set(), hidden_python, "项目源码被忽略规则隐藏，先修正规则而非强行添加文件")

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
