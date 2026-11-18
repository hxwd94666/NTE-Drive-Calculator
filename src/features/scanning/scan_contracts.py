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


def vision_cancel_message(parsed_count: int, *, text_catalog=None) -> str:
    return (
        localized_text(text_catalog, 'auto.features.scanning.scan_contracts.vision_cancel_message.f83ba2e3d', '已停止继续解析，本次已解析 {value} 张截图。\n\n由于解析任务已取消，本次结果未写入/更新 SQLite 背包快照。', value=f'{int(parsed_count or 0)}')
    )
from src.domain.localization.catalog import localized_text
