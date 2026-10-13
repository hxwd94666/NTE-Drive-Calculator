# 独立原生分析程序的显式批量公式后端契约。
from __future__ import annotations
from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable


class BuffProjectionBatchTooLarge(ValueError):
    """精确投影批次超出协议上限，调用方须分批而非丢弃命中。"""


class DirectFormulaBackend(Protocol):
    """按顺序计算冻结输入；错误传播，不回退为另一公式实现。"""

    def calculate_batch(
        self,
        inputs: Sequence[dict[str, Any]],
        *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]: ...


@runtime_checkable
class BuffProjectionBackend(Protocol):
    """批量投影冻结逐击和区间表，不在 Python 重算 Buff 规则。"""

    def project_batch(
        self, payload: dict[str, Any], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]: ...


@runtime_checkable
class BuffProjectionPlanBackend(Protocol):
    """选择时间和受益范围后投影完整冻结候选，不补读活状态。"""

    @property
    def supports_projection_plan(self) -> bool: ...

    def project_plan_batch(
        self, payload: dict[str, Any], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]: ...


@runtime_checkable
class BattleComputeBackend(Protocol):
    """在独立 Core 内顺序计算冻结批次，能力与异常由显式后端提供。"""

    @property
    def supports_battle_compute(self) -> bool: ...

    def compute_batch(
        self, operation: str, inputs: Sequence[dict[str, Any]], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]: ...


def available_battle_compute(backend: object) -> BattleComputeBackend | None:
    """只消费组合根显式注入且声明就绪的计算能力，不另建连接或 Python 回退。"""
    if isinstance(backend, BattleComputeBackend) and backend.supports_battle_compute:
        return backend
    return None
