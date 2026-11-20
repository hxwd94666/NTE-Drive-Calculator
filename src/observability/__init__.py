# 公开核心功能结构化日志的稳定入口。
"""Structured operation logging without feature or UI dependencies."""

from src.observability.context import OperationContext
from src.observability.events import validate_event_name
from src.observability.operation import OperationSpan, log_event, operation_scope
from src.observability.redaction import redact_log_fields, safe_exception, user_id_hash

__all__ = [
    "OperationContext",
    "OperationSpan",
    "log_event",
    "operation_scope",
    "redact_log_fields",
    "safe_exception",
    "user_id_hash",
    "validate_event_name",
]
