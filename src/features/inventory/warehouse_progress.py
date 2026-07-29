# 管理仓库状态写回期间的模态进度反馈与页面忙碌状态。
"""Warehouse state-write progress UI kept separate from warehouse behavior."""

from __future__ import annotations

from collections.abc import Callable
from queue import Empty, SimpleQueue
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from src.utils.logger import logger


def close_warehouse_state_progress(window: Any) -> None:
    timer = getattr(window, "_warehouse_state_progress_timer", None)
    if timer is not None:
        timer.stop()
    dialog = getattr(window, "_warehouse_state_progress_dialog", None)
    if dialog is not None:
        dialog.close()
        dialog.deleteLater()
    window._warehouse_state_progress_timer = None
    window._warehouse_state_progress_dialog = None


def show_warehouse_state_progress(
    window: Any,
    *,
    change_count: int,
) -> Callable[[str], None]:
    """Show an indeterminate modal dialog and return a worker-safe reporter."""
    close_warehouse_state_progress(window)
    messages: SimpleQueue[str] = SimpleQueue()
    dialog = QProgressDialog(
        f"正在准备写入 {change_count} 件装备状态…",
        "",
        0,
        0,
        window,
    )
    dialog.setWindowTitle("仓库状态修改进度")
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setCancelButton(None)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setMinimumDuration(0)
    dialog.setMinimumWidth(480)

    timer = QTimer(dialog)

    def update_message() -> None:
        latest: str | None = None
        while True:
            try:
                latest = messages.get_nowait()
            except Empty:
                break
        if latest:
            dialog.setLabelText(latest)

    timer.timeout.connect(update_message)
    timer.start(80)
    window._warehouse_state_progress_dialog = dialog
    window._warehouse_state_progress_timer = timer
    dialog.show()
    return messages.put


def set_warehouse_management_busy(
    window: Any,
    busy: bool,
    hint: str = "",
) -> None:
    if hasattr(window, "warehouse_manage_btn"):
        window.warehouse_manage_btn.setEnabled(not busy)
    if hasattr(window, "warehouse_save_btn"):
        window.warehouse_save_btn.setEnabled(not busy)
    for name in (
        "warehouse_normal_btn",
        "warehouse_lock_btn",
        "warehouse_discard_btn",
    ):
        button = getattr(window, name, None)
        if button is not None:
            selected = bool(
                window.warehouse_view.selectionModel().selectedIndexes()
            )
            button.setEnabled(not busy and selected)
    if busy and hasattr(window, "warehouse_hint"):
        window.warehouse_hint.setText(hint)
        window.warehouse_hint.show()


def update_warehouse_save_state(window: Any) -> None:
    pending_count = len(
        getattr(window, "_warehouse_pending_state_changes", {})
    )
    if hasattr(window, "warehouse_save_btn"):
        window.warehouse_save_btn.setEnabled(True)
        window.warehouse_save_btn.setText(
            f"保存 ({pending_count})" if pending_count else "保存"
        )


def on_warehouse_state_error(window: Any, error: str) -> None:
    close_warehouse_state_progress(window)
    window._set_warehouse_management_busy(False)
    logger.error(f"仓库状态管理失败: {error}")
    QMessageBox.critical(
        window,
        "仓库管理失败",
        f"未能完成一键弃置/锁定：\n{error}",
    )
