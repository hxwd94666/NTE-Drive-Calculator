# 提供统一的基准测量契约：预热、多轮、取中位与最小值。
"""Stable benchmark harness for performance claims.

本机基准噪声很大，单次运行可能相差 50% 以上，因此所有性能结论都必须来自
「预热 + 多轮 + 中位/最小」，并给出改动前后两次运行的数字。
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Callable


def measure(
    label: str,
    fn: Callable[[], object],
    *,
    rounds: int = 5,
    warmup: int = 1,
) -> dict[str, float]:
    """Run ``fn`` and return stable timing statistics in milliseconds."""

    if rounds < 1:
        raise ValueError("rounds 必须大于等于 1")
    for _ in range(max(0, warmup)):
        fn()
    samples: list[float] = []
    for _ in range(rounds):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "median_ms": statistics.median(samples),
        "min_ms": min(samples),
        "max_ms": max(samples),
        "total_ms": sum(samples),
    }


def format_result(label: str, stats: dict[str, float]) -> str:
    """Render one measurement as a single stable line."""

    return (
        f"{label}: 中位={stats['median_ms']:.1f}ms "
        f"最小={stats['min_ms']:.1f}ms 最大={stats['max_ms']:.1f}ms "
        f"合计={stats['total_ms']:.1f}ms"
    )


def run_scenarios(
    scenarios: dict[str, Callable[[], object]],
    *,
    rounds: int = 5,
    warmup: int = 1,
) -> dict[str, dict[str, float]]:
    """Measure every scenario, printing results as they complete."""

    results: dict[str, dict[str, float]] = {}
    for label, scenario in scenarios.items():
        stats = measure(label, scenario, rounds=rounds, warmup=warmup)
        results[label] = stats
        print(format_result(label, stats), flush=True)
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 NTE 端到端性能基准")
    parser.add_argument(
        "--rounds", type=int, default=5, help="每个场景的测量轮数（默认 5）",
    )
    parser.add_argument(
        "--warmup", type=int, default=1, help="预热轮数（默认 1）",
    )
    parser.add_argument(
        "--only", action="append", default=None,
        help="只运行指定场景（可重复；默认全部）",
    )
    return parser


def select_scenarios(
    scenarios: dict[str, Callable[[], object]],
    only: list[str] | None,
) -> dict[str, Callable[[], object]]:
    """Filter scenarios by name while keeping the declared order."""

    if not only:
        return scenarios
    missing = [name for name in only if name not in scenarios]
    if missing:
        raise SystemExit(f"未知场景：{', '.join(missing)}")
    return {name: scenarios[name] for name in scenarios if name in only}
