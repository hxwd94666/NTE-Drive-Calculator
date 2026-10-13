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
    # 兼容尚未迁移的平铺夹具；正式测试包身份由文件位置唯一确定。
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(ROOT / "tests"), environment.get("PYTHONPATH", "")]
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
    for directory in (ROOT, ROOT / "tests"):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    files = sorted((tests_root or ROOT / "tests").rglob(args.pattern))
    if not files:
        print("没有匹配的测试文件；本次验证未执行", file=sys.stderr)
        return int(pytest.ExitCode.NO_TESTS_COLLECTED)
    selection = TierSelection(args.tier, args.pattern)
    arguments = ["-v" if args.verbose else "-q", *map(str, files)]
    if args.collect_only or jobs > 1:
        arguments.insert(0, "--collect-only")
    # pytest 原生支持 TestCase；旧 load_tests 仅是同文件 pytest 包装，无须重复执行。
    result = int(pytest.main(arguments, plugins=[selection]))
    if result or args.collect_only or jobs == 1:
        return result
    if not selection.modules:
        return int(pytest.ExitCode.NO_TESTS_COLLECTED)
    return run_parallel(selection, jobs=jobs, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
