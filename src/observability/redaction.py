# 对日志字段、异常和外部地址执行统一脱敏与尺寸限制。
"""Redact secrets and user paths before structured values reach a sink."""

from __future__ import annotations

import re
import traceback
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
# 单引号是 URL 合法字符；宁可连同外层引用遮盖，也不截断鉴权值后留下尾部。
_URL = re.compile(r'''https?://[^\s<>"]+''', re.IGNORECASE)


def _sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    # 只遮盖明确禁记的业务正文/身份；uid_count 等聚合量和内部 account_id 仍可诊断。
    forbidden = {
        "uid", "uids", "uid_list", "payload", "rpc", "raw_rpc", "full_rpc",
        "account_name", "account_display_name", "ocr_text", "screenshot",
        "raw_payload", "rpc_payload", "request_payload", "response_payload",
        "equipment_uids", "drive_uids", "tape_uids", "item_uids",
    }
    return (
        normalized in forbidden
        or any(part in normalized for part in _SENSITIVE_KEY_PARTS)
    )


def _sanitize_url(value: str) -> str:
    if not value.lower().startswith(("http://", "https://")):
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        # 畸形外部地址可能仍含凭据；解析失败也不能原文回落。
        return "<redacted-url>"
    # userinfo 与任意查询/片段都可能携带鉴权值；主机、端口和路径用于诊断。
    netloc = parts.netloc.rsplit("@", 1)[-1]
    query = _REDACTED if parts.query or parts.fragment else ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


def _sanitize_text(value: str) -> str:
    # 先清洗所有嵌入 URL，避免只处理消息开头，或让凭据正则截断 URL 后留下尾部。
    sanitized = sanitize_local_log_text(value)
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


def sanitize_local_log_text(value: str) -> str:
    """Keep local file paths for troubleshooting, but remove common credentials."""

    sanitized = _URL.sub(lambda match: _sanitize_url(match.group(0)), value)
    sanitized = _BEARER.sub(f"Bearer {_REDACTED}", sanitized)
    sanitized = _KEY_VALUE_SECRET.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}",
        sanitized,
    )
    return sanitized


def format_local_exception(error: BaseException) -> str:
    """Log a bounded exception and frame locations without source lines or locals."""

    message = " ".join(sanitize_local_log_text(str(error)).split())[:1000]
    frames = traceback.extract_tb(error.__traceback__)[-8:]
    locations = " <- ".join(
        f"{frame.filename}:{frame.lineno}:{frame.name}" for frame in frames
    )
    details = f"{type(error).__name__}: {message}"
    if locations:
        details += f" | stack={sanitize_local_log_text(locations)}"
    return details
