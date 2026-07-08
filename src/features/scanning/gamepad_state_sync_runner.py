# 执行扫描后装备状态的手柄同步。
"""Run gamepad state sync after post-scan evaluation."""

from __future__ import annotations

import time
from typing import Callable

from src.features.scanning.post_actions import summarize_state_changes
from src.utils.logger import logger


class GamepadStateSyncRunner:
    def __init__(
        self,
        scanner,
        *,
        total_drives: int,
        post_action_ready_callback: Callable[[], None] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.scanner = scanner
        self.total_drives = int(total_drives)
        self.post_action_ready_callback = post_action_ready_callback
        self.sleep_fn = sleep_fn

    def sync(self, state_changes: list[dict], effective_config: dict | None) -> dict[str, int]:
        if not state_changes:
            return summarize_state_changes([])
        if not hasattr(self.scanner, "sync_equipment_states"):
            logger.warning("扫描后管理目标已生成，但当前扫描器不支持状态同步，已跳过游戏内处理。")
            return summarize_state_changes(state_changes, 0)

        self._notify_ready()
        action_mode = "hmt" if (effective_config or {}).get("server_region") == "hmt" else "default"
        applied_count = self.scanner.sync_equipment_states(
            self.total_drives,
            state_changes,
            action_mode=action_mode,
        )
        return summarize_state_changes(state_changes, applied_count)

    def _notify_ready(self) -> None:
        if self.post_action_ready_callback is None:
            return
        self.post_action_ready_callback()
        self.sleep_fn(0.35)
