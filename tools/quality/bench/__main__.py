# 运行全部端到端基准场景：目录重建、静态库开连、评分与快照写入。
"""CLI entry point: ``uv run python -m tools.quality.bench``."""

from __future__ import annotations

from .harness import build_parser, run_scenarios, select_scenarios
from .scenarios import build_scenarios


def main() -> int:
    args = build_parser().parse_args()
    scenarios = build_scenarios()
    cleanup = scenarios.pop("cleanup")
    try:
        run_scenarios(
            select_scenarios(scenarios, args.only),
            rounds=args.rounds,
            warmup=args.warmup,
        )
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
