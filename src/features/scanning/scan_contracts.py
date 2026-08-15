# 提供扫描工作流的纯状态与文案契约。
"""Small pure helpers shared by the scanning controller and workflow."""

from __future__ import annotations


def scanning_is_running(owner) -> bool:
    for name in ("_scan_worker", "_gamepad_worker", "_vision_worker"):
        worker = getattr(owner, name, None)
        if worker is not None and callable(getattr(worker, "isRunning", None)):
            if worker.isRunning():
                return True
    return False


def offline_scope_replaces_inventory(scope: str) -> bool:
    return scope in ("full", "all")


def vision_cancel_message(parsed_count: int) -> str:
    return (
        f"已停止继续解析，本次已解析 {int(parsed_count or 0)} 张截图。\n\n"
        "由于解析任务已取消，本次结果未写入/更新 SQLite 背包快照。"
    )
