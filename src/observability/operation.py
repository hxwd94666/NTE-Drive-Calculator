# 记录具有统一事件名、关联标识、结果和耗时的业务操作。
"""Explicit operation events layered on top of the shared Loguru logger."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Iterator

from src.observability.context import OperationContext
from src.observability.events import validate_event_name
from src.observability.redaction import redact_log_fields, safe_exception
from src.utils.logger import logger


def _display_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def log_event(
    level: str,
    event: str,
    message: str,
    context: OperationContext,
    **fields: object,
) -> None:
    """Write one structured event while keeping the file human-readable."""

    normalized_event = validate_event_name(event)
    safe_fields = redact_log_fields({**context.as_fields(), **fields})
    context_text = " ".join(
        f"{key}={_display_value(value)}" for key, value in sorted(safe_fields.items())
    )
    safe_message = str(redact_log_fields({"message": message})["message"])
    logger.bind(event=normalized_event, **safe_fields).log(
        str(level).upper(),
        f"{normalized_event} | {safe_message} | {context_text}",
    )


@dataclass(slots=True)
class OperationSpan:
    """Mutable result annotations owned only by one active scope."""

    _result_fields: dict[str, object] = field(default_factory=dict)

    def annotate(self, **fields: object) -> None:
        self._result_fields.update(fields)


@contextmanager
def operation_scope(
    context: OperationContext,
    *,
    started_event: str,
    succeeded_event: str,
    failed_event: str,
    message: str,
    **fields: object,
) -> Iterator[OperationSpan]:
    """Log one operation's start, success or failure with a shared ID."""

    validate_event_name(started_event)
    validate_event_name(succeeded_event)
    validate_event_name(failed_event)
    started_at = perf_counter()
    log_event("INFO", started_event, message, context, phase="started", **fields)
    span = OperationSpan()
    try:
        yield span
    except Exception as error:
        duration_ms = round((perf_counter() - started_at) * 1000, 3)
        log_event(
            "ERROR",
            failed_event,
            f"{message}失败",
            context,
            phase="failed",
            duration_ms=duration_ms,
            result="failed",
            error=safe_exception(error),
            **span._result_fields,
        )
        raise
    else:
        duration_ms = round((perf_counter() - started_at) * 1000, 3)
        log_event(
            "INFO",
            succeeded_event,
            f"{message}完成",
            context,
            phase="succeeded",
            duration_ms=duration_ms,
            result="succeeded",
            **span._result_fields,
        )
