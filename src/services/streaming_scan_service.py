# 串联全量扫描截图与后台解析，减少用户总等待时间。
"""Streaming scan/parse pipeline for full visual scans."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.domain.post_actions import (
    merge_post_action_config,
    post_actions_enabled,
    summarize_state_changes,
)
from src.integrations.vision import equipment_state_detection
from src.integrations.vision.equipment_state_detection import right_panel_button_state_from_image
from src.services.gamepad_state_sync_service import GamepadStateSyncService


# Compatibility exports for existing state-image callers. Detection ownership stays
# in the vision integration, while this service continues to expose its prior ratios.
TRASH_BUTTON_CENTER = equipment_state_detection.TRASH_BUTTON_CENTER
LOCK_BUTTON_CENTER = equipment_state_detection.LOCK_BUTTON_CENTER
from src.services.post_action_evaluator import PostActionEvaluator
from src.services.scan_inventory_commit_service import ScanInventoryCommitService
from src.services.scan_parse_coordinator import CAPTURE_QUEUE_MAXSIZE, ScanParseCoordinator
from src.scanner.window_capture import crop_window_border_from_image
from src.utils.image_io import imread_unicode
from src.utils.logger import logger


def _parse_during_scan_enabled() -> bool:
    import os

    value = os.environ.get("NTE_STREAMING_SCAN_PARSE", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


GRADE_ORDER = ["ACE", "SSS", "SS", "S", "A", "B", "C", "D"]
def _equipment_screenshot_state(image_path: str) -> str:
    try:
        img = imread_unicode(image_path)
        if img is None:
            return "normal"
        img = crop_window_border_from_image(img)
        state = right_panel_button_state_from_image(img)
        return state if state in {"normal", "locked", "discarded"} else "normal"
    except Exception as exc:
        logger.debug(f"装备状态图标检测失败，按普通处理: {image_path} | {exc}")
        return "normal"


def run_streaming_scan_parse(
    scanner: Any,
    processor: Any,
    total_drives: int,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    scan_done_callback: Callable[[int, int], None] | None = None,
    parse_done_callback: Callable[[], None] | None = None,
    post_action_ready_callback: Callable[[], None] | None = None,
    post_actions_config: dict[str, Any] | None = None,
    selected_roles: list[str] | None = None,
    config_dir: str | Path | None = None,
    user_database_path: str | Path | None = None,
    parse_during_scan: bool | None = None,
    low_load_mode: bool = False,
    low_load_parse_delay_seconds: float = 0.12,
    capture_queue_maxsize: int = CAPTURE_QUEUE_MAXSIZE,
    allow_post_actions: bool = True,
    result_is_current: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run a full visual scan while parsing captured screenshots in a consumer thread."""

    if parse_during_scan is None:
        parse_during_scan = _parse_during_scan_enabled()
    if low_load_mode:
        parse_during_scan = False

    scan_result = ScanParseCoordinator(
        scanner,
        processor,
        total_drives,
        progress_callback=progress_callback,
        scan_done_callback=scan_done_callback,
        parse_during_scan=bool(parse_during_scan),
        low_load_mode=bool(low_load_mode),
        low_load_parse_delay_seconds=float(low_load_parse_delay_seconds),
        capture_queue_maxsize=int(capture_queue_maxsize),
        state_detector=lambda path: _equipment_screenshot_state(path),
        sleep_fn=time.sleep,
    ).run()
    if parse_done_callback is not None:
        parse_done_callback()

    if result_is_current is not None and not result_is_current():
        stats = scan_result.to_stats()
        stats["discarded_stale"] = True
        return stats

    effective_post_config = post_actions_config
    effective_post_config = merge_post_action_config(effective_post_config) if effective_post_config else None
    if cancel_check is not None and cancel_check():
        logger.warning("流水线扫描已取消，解析结果不会写入库存。")
        post_action_filter_summary = {}
        post_action_summary = summarize_state_changes([])
    elif scan_result.captured_count == int(total_drives):
        ScanInventoryCommitService(processor, scanner).commit()
        if result_is_current is not None and not result_is_current():
            stats = scan_result.to_stats()
            stats["discarded_stale"] = True
            return stats
        if allow_post_actions:
            evaluation = PostActionEvaluator(
                post_actions_config=effective_post_config,
                selected_roles=selected_roles,
                config_dir=config_dir,
                user_database_path=user_database_path,
            ).evaluate(scan_result.parsed_items, processor.inventory)
            post_action_summary = GamepadStateSyncService(
                scanner,
                total_drives=total_drives,
                post_action_ready_callback=post_action_ready_callback,
                sleep_fn=time.sleep,
            ).sync(evaluation.state_changes, evaluation.config)
            post_action_filter_summary = evaluation.filter_summary
            effective_post_config = evaluation.config
        else:
            effective_post_config = None
            post_action_filter_summary = {}
            post_action_summary = summarize_state_changes([])
    else:
        post_action_filter_summary = {}
        post_action_summary = summarize_state_changes([])

    stats = scan_result.to_stats()
    if effective_post_config and post_actions_enabled(effective_post_config):
        stats["post_actions_enabled"] = True
        stats.update(post_action_filter_summary)
        stats.update(post_action_summary)
    return stats
