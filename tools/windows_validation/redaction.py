# 对 Windows 验证报告中的凭据、账号和本机路径进行保守脱敏。
"""Redact sensitive values before evidence is written to disk."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_KEY_SECRET_PATTERN = re.compile(
    r"(?i)\b(token|cdk|authorization|api[_-]?key)\b\s*[:=]\s*\S+"
)
_AUTH_URL_PATTERN = re.compile(
    r"(?i)https?://\S*(?:token|cdk|signature|auth)=\S+"
)


def redact_text(value: str, *, roots: tuple[Path, ...] = ()) -> str:
    redacted = value
    redacted = _KEY_SECRET_PATTERN.sub(
        lambda match: f"{match.group(1)}=<redacted>",
        value,
    )
    redacted = _AUTH_URL_PATTERN.sub("<redacted-url>", redacted)
    for root in roots:
        raw = str(root)
        if raw:
            redacted = redacted.replace(raw, "<path>")
    return redacted


def redact_value(value: Any, *, roots: tuple[Path, ...] = ()) -> Any:
    if isinstance(value, str):
        return redact_text(value, roots=roots)
    if isinstance(value, dict):
        return {
            str(key): redact_value(item, roots=roots)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item, roots=roots) for item in value]
    return value
