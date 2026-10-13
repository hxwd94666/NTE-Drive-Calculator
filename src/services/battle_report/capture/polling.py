# 实时战报查询超时保留采集会话并重试，真实进程及协议错误继续上交。
from __future__ import annotations

import threading
from collections.abc import Callable

from src.integrations.nte_core_protocol import NteCoreTimeoutError
from src.observability import OperationContext
from src.observability.operation import log_event


def poll_battle_until_stopped(
    poll: Callable[[], object], *, stop_event: threading.Event,
    operation: OperationContext, notify: Callable[[str], None],
) -> None:
    failures = 0
    while not stop_event.wait(0.5):
        try:
            poll()
        except NteCoreTimeoutError as error:
            if error.method not in {"battle.get_record", "battle.get_axis"}:
                raise
            if stop_event.is_set():
                break
            failures += 1
            if failures == 1:
                notify("战报数据查询延迟，正在重试；采集会话保持开启。")
                log_event("WARNING", "battle_report.live_query_delayed", "实时战报查询超时，保留会话并重试",
                          operation, method=error.method, timeout_seconds=error.timeout)
            continue
        if failures and not stop_event.is_set():
            notify("战报查询已恢复，继续采集。")
            log_event("INFO", "battle_report.live_query_recovered", "实时战报查询已恢复",
                      operation, timeout_count=failures)
            failures = 0
