# 执行扫描后装备状态的手柄同步。
"""Application service for gamepad state sync after scan evaluation."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from src.domain.post_actions import summarize_state_changes
from src.utils.logger import logger


class GamepadStateSyncService:
    def __init__(
        self,
        scanner: Any,
        *,
        total_drives: int,
        post_action_ready_callback: Callable[[], None] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.scanner = scanner
        self.total_drives = int(total_drives)
        self.post_action_ready_callback = post_action_ready_callback
        self.sleep_fn = sleep_fn

    def sync(
        self,
        state_changes: list[dict[str, Any]],
        effective_config: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not state_changes:
            logger.info("[状态管理] 没有需要同步的游戏内状态变更")
            return summarize_state_changes([])
        if not hasattr(self.scanner, "sync_equipment_states"):
            logger.warning("扫描后管理目标已生成，但当前扫描器不支持状态同步，已跳过游戏内处理。")
            return summarize_state_changes(state_changes, 0)

        self._notify_ready()
        action_mode = "hmt" if (effective_config or {}).get("server_region") == "hmt" else "default"
        logger.info(
            f"[状态管理] 开始游戏内同步: total={self.total_drives} "
            f"targets={len(state_changes)} mode={action_mode}"
        )
        for change in state_changes:
            logger.info(
                f"[状态管理] 同步队列 raw_drive_{int(change.get('index', 0)):04d} "
                f"{change.get('current_state')} -> {change.get('target_state')} "
                f"uid={change.get('uid')} type={change.get('item_type')}"
            )
        sync_result = self.scanner.sync_equipment_states(
            self.total_drives,
            state_changes,
            action_mode=action_mode,
        )
        applied_count = int(getattr(sync_result, "applied_count", sync_result))
        logger.info(f"[状态管理] 游戏内同步完成: requested={len(state_changes)} applied={applied_count}")
        summary: dict[str, Any] = summarize_state_changes(state_changes, applied_count)
        mismatches = tuple(getattr(sync_result, "state_mismatches", ()) or ())
        if mismatches:
            indexes = tuple(int(mismatch.index) for mismatch in mismatches)
            summary["post_action_state_mismatch_count"] = len(indexes)
            summary["post_action_state_mismatch_indexes"] = indexes
            logger.warning(f"[状态管理] 因当前状态不一致跳过: indexes={indexes}")
        return summary

    def _notify_ready(self) -> None:
        if self.post_action_ready_callback is None:
            return
        self.post_action_ready_callback()
        self.sleep_fn(0.35)
