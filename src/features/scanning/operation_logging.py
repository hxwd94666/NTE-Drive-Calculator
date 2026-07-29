# 维护扫描任务的关联上下文、统一终止事件和活动状态。
"""Operation logging helpers for scanning controller callbacks."""

from __future__ import annotations

from src.features.scanning.dependencies import ScanningDependencies
from src.observability.context import OperationContext
from src.observability.operation import log_event


def begin_scan_operation(
    owner,
    dependencies: ScanningDependencies,
    *,
    route: str,
    **fields: object,
) -> OperationContext:
    operation = OperationContext.create(
        "scanning",
        account_id=dependencies.account_id,
        context_generation=dependencies.generation,
    )
    owner._scan_operation_context = operation
    owner._scan_operation_active = True
    log_event(
        "INFO",
        "scanning.started",
        "开始扫描或解析库存",
        operation,
        route=route,
        **fields,
    )
    return operation


def scan_event(
    owner,
    level: str,
    event: str,
    message: str,
    **fields: object,
) -> None:
    operation = getattr(owner, "_scan_operation_context", None)
    if operation is None:
        dependencies = getattr(owner, "_scan_dependencies", None)
        dependencies = dependencies or ScanningDependencies.from_app_context(
            owner.app_context
        )
        operation = begin_scan_operation(
            owner, dependencies, route="unknown"
        )
    log_event(level, event, message, operation, **fields)
    if event in {
        "scanning.succeeded",
        "scanning.failed",
        "scanning.cancelled",
    }:
        owner._scan_operation_active = False

