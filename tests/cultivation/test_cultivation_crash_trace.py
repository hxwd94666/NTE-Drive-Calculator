# 验证养成计算追踪日志仅包含阶段与计数，不携带用户业务内容。
"""Diagnostic marker contract without account or material payloads."""

from __future__ import annotations

from src.utils import cultivation_trace


def test_cultivation_marker_contains_only_local_operation_and_counts() -> None:
    messages: list[str] = []
    sink = cultivation_trace.logger.add(
        lambda message: messages.append(message.record["message"]),
        filter=lambda record: record["message"].startswith("cultivation.trace |"),
    )
    try:
        cultivation_trace.trace_cultivation(3, "ui.result_rendered", targets=2)
    finally:
        cultivation_trace.logger.remove(sink)

    assert len(messages) == 1
    assert "cultivation.trace | op=3" in messages[0]
    assert "phase=ui.result_rendered targets=2" in messages[0]
    assert "account" not in messages[0]
    assert "item_id" not in messages[0]
