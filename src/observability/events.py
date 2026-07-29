# 校验机器可检索的稳定日志事件名称。
"""Stable event-name validation shared by operation logging."""

from __future__ import annotations

import re


_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


def validate_event_name(event: str) -> str:
    normalized = str(event).strip()
    if not _EVENT_NAME.fullmatch(normalized):
        raise ValueError(
            "日志 event 必须是至少两段的小写点分名称，且每段只能包含字母、数字和下划线"
        )
    return normalized
