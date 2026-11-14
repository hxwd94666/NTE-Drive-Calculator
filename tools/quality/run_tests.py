# 统一执行 pytest 函数与 unittest 行为用例，按真实收集结果选择核心层级和整模块分片。
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import os
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
CORE_MODULE_TOKENS = (
    "_boundaries", "_context", "_dao", "_database", "_logging", "_migration",
    "_optimizer", "_service", "_snapshot", "test_allocation_", "test_dependency_",
    "test_observability_", "test_repository_hygiene", "test_type_annotation_",
)


def _path_key(path: Path) -> str:
    # 测试文件由本次 rglob 冻结；比较字面绝对路径，不触发 Windows 文件 ID 查询。
    return os.path.normcase(os.path.abspath(path))


class FrozenPythonFiles:
    """一个目录参数承载完整冻结文件集，避免 Windows 对数百初始参数做二次匹配。"""

    def __init__(self, files: list[Path]) -> None:
        self.files = {_path_key(path): path for path in files}
        self.directories = {_path_key(parent) for path in files for parent in path.parents}
        self.seen: set[str] = set()

    @pytest.hookimpl(tryfirst=True)
    def pytest_ignore_collect(self, collection_path: Path):
        key = _path_key(collection_path)
        # 与显式文件参数相同：已选文件及祖先不受默认文件名／隐藏目录过滤。
        # 收集期间新生成的文件不进入本次请求，未选文件也不被顺带导入。
        return key not in self.files and key not in self.directories

    def pytest_collectstart(self, collector) -> None:
        key = _path_key(collector.path)
        if key in self.files:
            self.seen.add(key)

    def pytest_collection_finish(self, session) -> None:
        missing = self.files.keys() - self.seen
        if missing and not session.testsfailed:
            names = ", ".join(str(self.files[key]) for key in sorted(missing)[:8])
            raise pytest.UsageError(f"冻结测试文件未完成收集（{len(missing)} 项）：{names}")


class TierSelection:
    """只过滤已成功收集的用例；导入与收集错误仍由 pytest 原样传播。"""

    def __init__(self, tier: str, pattern: str) -> None:
        self.tier = tier
        self.pattern = pattern
        self.modules: Counter[str] = Counter()

    def pytest_collection_modifyitems(self, config, items) -> None:
        selected, excluded = [], []
        for item in items:
            marker = getattr(item.module, "NTE_TEST_TIER", None)
            core = (
                str(marker).casefold() == "core" if marker is not None
                else any(token in item.path.stem.casefold() for token in CORE_MODULE_TOKENS)
            )
            # 专项 pattern 已显式指定范围，沿用原入口不再二次裁减的语义。
            target = selected if self.tier == "full" or self.pattern != "test*.py" or core else excluded
            target.append(item)
        items[:] = selected
        config.hook.pytest_deselected(items=excluded)
        self.modules = Counter(str(item.path) for item in selected)


def module_shards(counts: Counter[str], jobs: int) -> list[list[str]]:
    """按真实 node 数均衡整文件；同一模块及其 fixtures 只属于一个进程。"""
    shards: list[list[str]] = [[] for _ in range(min(jobs, len(counts)))]
    loads = [0] * len(shards)
    for path, count in sorted(counts.items(), key=lambda row: (-row[1], row[0])):
        target = min(range(len(shards)), key=lambda index: (loads[index], index))
        shards[target].append(path)
        loads[target] += count
    return shards


def run_parallel(selection: TierSelection, *, jobs: int, verbose: bool) -> int:
    """收集成功才启动子进程；任何 shard 非零都保留为整次验证失败。"""
    shards = module_shards(selection.modules, jobs)
    environment = os.environ.copy()
    # 夹具只通过 tests 包的完整模块名共享，不为根目录裸导入增加第二身份。
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), environment.get("PYTHONPATH", "")]
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "pytest", "-v" if verbose else "-q", *shard],
            cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        for shard in shards
    ]
    success = True
    for index, process in enumerate(processes, 1):
        output, _ = process.communicate()
        print(f"[{selection.tier} shard {index}/{len(shards)}]", flush=True)
        print(output.rstrip(), flush=True)
        success = process.returncode == 0 and success
    print(f"[{selection.tier}] {sum(selection.modules.values())} collected cases; {len(shards)} shards")
    return 0 if success else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 NTE 核心或全量行为测试")
    parser.add_argument("tier", choices=("core", "full"))
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--pattern", default="test*.py", help="递归发现匹配文件名的专项测试")
    parser.add_argument("--collect-only", action="store_true", help="仅核对真实用例发现，不执行测试")
    parser.add_argument("-j", "--jobs", type=int, default=None, help="core 默认 3，full 默认单进程")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, tests_root: Path | None = None) -> int:
    args = parse_args(argv)
    jobs = args.jobs if args.jobs is not None else (3 if args.tier == "core" else 1)
    if jobs < 1:
        raise SystemExit("--jobs 必须大于等于 1")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    directory = tests_root or ROOT / "tests"
    files = sorted(directory.rglob(args.pattern))
    if not files:
        print("没有匹配的测试文件；本次验证未执行", file=sys.stderr)
        return int(pytest.ExitCode.NO_TESTS_COLLECTED)
    selection = TierSelection(args.tier, args.pattern)
    plugins = [selection]
    if all(path.suffix == ".py" for path in files):
        # 仍以相同 rglob 结果为唯一范围，不改 core/full 规则或漏掉非 core 收集错误。
        plugins.append(FrozenPythonFiles(files))
        arguments = ["-v" if args.verbose else "-q", "-o", "python_files=*.py", str(directory)]
    else:
        # 非 Python 自定义收集器继续消费原显式目标，不猜测其扩展名语义。
        arguments = ["-v" if args.verbose else "-q", *map(str, files)]
    if args.collect_only or jobs > 1:
        arguments.insert(0, "--collect-only")
    # pytest 原生支持函数与 TestCase，用同一收集结果分片，不另外加载一套入口。
    result = int(pytest.main(arguments, plugins=plugins))
    if result or args.collect_only or jobs == 1:
        return result
    if not selection.modules:
        return int(pytest.ExitCode.NO_TESTS_COLLECTED)
    return run_parallel(selection, jobs=jobs, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
