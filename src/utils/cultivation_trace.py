# 记录养成计算器崩溃排查所需的有限阶段标记，不采集用户材料与角色内容。
"""Compact cross-thread lifecycle markers for native-crash correlation."""

from __future__ import annotations

from threading import get_ident

from src.utils.logger import logger


def trace_cultivation(operation: int, phase: str, **counts: int | bool) -> None:
    """Write a synchronous phase marker to the existing bounded runtime log."""

    metrics = " ".join(
        f"{key}={int(value)}" for key, value in sorted(counts.items())
    )
    logger.info(
        f"cultivation.trace | op={int(operation)} thread={get_ident()} "
        f"phase={phase}" + (f" {metrics}" if metrics else "")
    )


__all__ = ["trace_cultivation"]
