# 维护单件鉴定操作的关联上下文和终止事件状态。
"""Operation logging helpers for identification controller callbacks."""

from __future__ import annotations

from src.features.identification.dependencies import IdentificationDependencies
from src.observability.context import OperationContext
from src.observability.operation import log_event


def begin_identification_operation(
    owner,
    dependencies: IdentificationDependencies,
    *,
    input_source: str,
) -> OperationContext:
    operation = OperationContext.create(
        "identification",
        account_id=dependencies.account_id,
        context_generation=dependencies.generation,
    )
    owner._identification_operation_context = operation
    owner._identification_operation_active = True
    log_event(
        "INFO",
        "identification.started",
        "开始单件鉴定",
        operation,
        input_source=input_source,
    )
    return operation


def identification_event(
    owner,
    level: str,
    event: str,
    message: str,
    **fields: object,
) -> None:
    operation = getattr(owner, "_identification_operation_context", None)
    if operation is None:
        dependencies = IdentificationDependencies.from_app_context(
            owner.app_context
        )
        operation = begin_identification_operation(
            owner, dependencies, input_source="unknown"
        )
    log_event(level, event, message, operation, **fields)
    if event in {
        "identification.succeeded",
        "identification.failed",
        "identification.cancelled",
    }:
        owner._identification_operation_active = False

