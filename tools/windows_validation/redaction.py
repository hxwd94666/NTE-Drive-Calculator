# 对 Windows 验证报告中的凭据、账号和本机路径进行保守脱敏。
"""报告落盘前统一处理自由文本、结构化敏感字段与路径键。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit


_SECRET_KEY = r"(?:(?:access[_-]?|refresh[_-]?)?token|cdk|authorization|api[_-]?key|auth|signature|sig|password|secret|credential|x-amz-(?:credential|signature|security-token))"
_SECRET_FIELD = re.compile(_SECRET_KEY, re.IGNORECASE)
_KEY_SECRET_PATTERN = re.compile(
    rf"\b({_SECRET_KEY})\b[\"']?\s*[:=]\s*"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|(?:Bearer|Basic)\s+[^\s,;]+|[^\s,;]+)",
    re.IGNORECASE,
)
_URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)  # 引号可属于凭据；边界存疑时整段保守脱敏。


def _redact_url(match: re.Match[str]) -> str:
    raw = match.group(0)
    try:
        url = urlsplit(raw)
        fields = parse_qsl(url.query, keep_blank_values=True) + parse_qsl(url.fragment, keep_blank_values=True)
        if url.username is not None or any(_SECRET_FIELD.fullmatch(key) for key, _ in fields):
            return "<redacted-url>"
    except ValueError:
        return "<redacted-url>"
    return raw


def redact_text(value: str, *, roots: tuple[Path, ...] = ()) -> str:
    # 先整段移除鉴权 URL，再处理键值；否则局部替换会掩盖 URL 的凭据标记。
    redacted = _URL_PATTERN.sub(_redact_url, value)
    redacted = _KEY_SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=<redacted>", redacted)
    for root in sorted(roots, key=lambda path: len(str(path)), reverse=True):
        raw = str(root)
        if raw:
            pattern = re.escape(raw.replace("\\", "/")).replace("/", r"[/\\]")
            redacted = re.sub(pattern, "<path>", redacted, flags=re.IGNORECASE)
    return redacted


def redact_value(value: Any, *, roots: tuple[Path, ...] = ()) -> Any:
    if isinstance(value, str):
        return redact_text(value, roots=roots)
    if isinstance(value, dict):
        # 敏感值常是纯字符串而非 key=value；同时脱敏文件证据用作字典键的路径。
        return {
            redact_text(str(key), roots=roots): (
                "<redacted>" if _SECRET_FIELD.fullmatch(str(key))
                else redact_value(item, roots=roots)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_value(item, roots=roots) for item in value]
    return value
