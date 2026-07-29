# 对日志字段、异常和外部地址执行统一脱敏与尺寸限制。
"""Redact secrets and user paths before structured values reach a sink."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


_REDACTED = "<redacted>"
_MAX_ITEMS = 20
_MAX_TEXT = 1000
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "password",
    "passwd",
    "secret",
    "token",
    "cookie",
    "cdk",
    "api_key",
    "apikey",
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_VALUE_SECRET = re.compile(
    r"(?i)\b(authorization|password|passwd|secret|token|cookie|cdk|api[_-]?key)\b"
    r"(\s*[:=]\s*)([^,\s;&]+)"
)
_WINDOWS_PATH = re.compile(r"(?i)(?:[A-Z]:\\)(?:[^\\\r\n]+\\)*([^\\\r\n]+)")


def _sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sanitize_url(value: str) -> str:
    if not value.lower().startswith(("http://", "https://")):
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.query and not parts.fragment:
        return value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "<redacted>", ""))


def _sanitize_text(value: str) -> str:
    sanitized = _BEARER.sub(f"Bearer {_REDACTED}", value)
    sanitized = _KEY_VALUE_SECRET.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}",
        sanitized,
    )
    if sanitized.lower().startswith(("http://", "https://")):
        sanitized = _sanitize_url(sanitized)
    sanitized = _WINDOWS_PATH.sub(lambda match: f"<path>\\{match.group(1)}", sanitized)
    if len(sanitized) > _MAX_TEXT:
        return sanitized[:_MAX_TEXT] + "…<truncated>"
    return sanitized


def _safe_value(value: Any) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Path):
        return value.name
    if isinstance(value, BaseException):
        return safe_exception(value)
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, Mapping):
        return redact_log_fields(value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = [_safe_value(item) for item in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            items.append(f"<{len(value) - _MAX_ITEMS} more>")
        return items
    return _sanitize_text(str(value))


def redact_log_fields(fields: Mapping[str, Any]) -> dict[str, object]:
    """Return a detached mapping safe enough for file and UI log sinks."""

    return {
        str(key): _REDACTED if _sensitive_key(key) else _safe_value(value)
        for key, value in fields.items()
    }


def safe_exception(error: BaseException) -> dict[str, str]:
    """Keep the exception type and a sanitized message, never object internals."""

    return {
        "error_type": type(error).__name__,
        "error_message": _sanitize_text(str(error)),
    }
