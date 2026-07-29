# 检查运行日志事件、操作标识、异常栈和独立时间戳日志生成情况。
"""Read-only runtime log inspection."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from tools.windows_validation.redaction import redact_text


_TIMESTAMP_LOG = re.compile(r"^nte_runtime_\d{8}_\d{6}(?:_\d+)?\.log$")
_OPERATION_ID = re.compile(r"\boperation_id=(?:\"[^\"]+\"|\S+)")


def timestamp_logs(log_dir: Path | None) -> tuple[Path, ...]:
    if log_dir is None or not log_dir.is_dir():
        return ()
    return tuple(
        sorted(
            path for path in log_dir.iterdir()
            if path.is_file() and _TIMESTAMP_LOG.match(path.name)
        )
    )


def inspect_logs(
    log_dir: Path | None,
    *,
    expected_events: Iterable[str] = (),
    roots: tuple[Path, ...] = (),
    excerpt_limit: int = 20,
) -> dict[str, object]:
    if log_dir is None:
        return {"configured": False}
    files = [
        path for path in (log_dir / "nte_runtime.log", *timestamp_logs(log_dir))
        if path.is_file()
    ]
    lines: list[str] = []
    for path in files:
        lines.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    expected = tuple(expected_events)
    matched = {event: any(event in line for line in lines) for event in expected}
    interesting = [
        redact_text(line, roots=roots)
        for line in lines
        if any(event in line for event in expected)
        or "ERROR" in line
        or "Traceback" in line
    ][-excerpt_limit:]
    return {
        "configured": True,
        "log_files": [path.name for path in files],
        "timestamp_log_count": len(timestamp_logs(log_dir)),
        "expected_events": matched,
        "operation_id_present": any(_OPERATION_ID.search(line) for line in lines),
        "traceback_present": any("Traceback" in line for line in lines),
        "excerpts": interesting,
    }

