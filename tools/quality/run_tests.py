# 提供核心与全量自动测试的统一入口。
"""Discover unittest cases and select a stable maintenance tier."""

from __future__ import annotations

import argparse
from collections import Counter
import subprocess
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CORE_MODULE_TOKENS = (
    "_boundaries",
    "_context",
    "_dao",
    "_database",
    "_logging",
    "_migration",
    "_optimizer",
    "_service",
    "_snapshot",
    "test_allocation_",
    "test_dependency_",
    "test_observability_",
    "test_repository_hygiene",
    "test_type_annotation_",
)


def iter_test_cases(
    suite: unittest.TestSuite,
) -> list[unittest.TestCase]:
    """Flatten nested discovery suites without depending on private unittest APIs."""

    cases: list[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            cases.extend(iter_test_cases(item))
        elif isinstance(item, unittest.TestCase):
            cases.append(item)
    return cases


def is_core_case(case: unittest.TestCase) -> bool:
    """Select core boundary tests by explicit module marker or stable filename role."""

    module_name = case.__class__.__module__
    module = sys.modules.get(module_name)
    tier = getattr(module, "NTE_TEST_TIER", None)
    if tier is not None:
        return str(tier).casefold() == "core"
    leaf = module_name.rsplit(".", 1)[-1].casefold()
    return any(token in leaf for token in CORE_MODULE_TOKENS)


def build_suite(tier: str) -> unittest.TestSuite:
    discovered = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"),
    )
    if tier == "full":
        return discovered
    selected = [
        case for case in iter_test_cases(discovered)
        if is_core_case(case)
    ]
    if not selected:
        raise RuntimeError("核心测试选择为空，请检查测试层级规则")
    return unittest.TestSuite(selected)


def balanced_module_shards(
    cases: Sequence[object],
    jobs: int,
) -> list[list[str]]:
    """Group whole test modules into stable, approximately even subprocesses."""

    counts = Counter(case.__class__.__module__ for case in cases)
    shard_count = max(1, min(int(jobs), len(counts)))
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    loads = [0] * shard_count
    for module_name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        target = min(range(shard_count), key=lambda index: (loads[index], index))
        import_name = module_name if "." in module_name else f"tests.{module_name}"
        shards[target].append(import_name)
        loads[target] += count
    return shards


def run_core_parallel(
    suite: unittest.TestSuite,
    *,
    jobs: int,
    verbose: bool,
) -> int:
    """Run independent core modules concurrently while preserving module isolation."""

    cases = iter_test_cases(suite)
    shards = balanced_module_shards(cases, jobs)
    processes: list[subprocess.Popen[str]] = []
    for shard in shards:
        command = [sys.executable, "-X", "utf8", "-m", "unittest"]
        if verbose:
            command.append("-v")
        command.extend(shard)
        processes.append(
            subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        )

    success = True
    for index, process in enumerate(processes, start=1):
        output, _ = process.communicate()
        print(f"[core shard {index}/{len(processes)}]")
        print(output.rstrip())
        success = success and process.returncode == 0
    print(f"[core] {len(cases)} tests across {len(shards)} parallel shards")
    return 0 if success else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 NTE 核心或全量自动测试")
    parser.add_argument("tier", choices=("core", "full"))
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=None,
        help="core 并行进程数；默认 3，full 默认保持单进程",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    suite = build_suite(args.tier)
    jobs = args.jobs if args.jobs is not None else (3 if args.tier == "core" else 1)
    if jobs < 1:
        raise SystemExit("--jobs 必须大于等于 1")
    if args.tier == "core" and jobs > 1:
        return run_core_parallel(suite, jobs=jobs, verbose=args.verbose)
    result = unittest.TextTestRunner(
        verbosity=2 if args.verbose else 1
    ).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
