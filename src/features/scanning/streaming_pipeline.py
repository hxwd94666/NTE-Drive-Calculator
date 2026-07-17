# 串联全量扫描截图与后台解析，减少用户总等待时间。
"""Streaming scan/parse pipeline for full gamepad scans."""

from __future__ import annotations

import time
from typing import Callable

import cv2
import numpy as np

from src.features.scanning.gamepad_state_sync_runner import GamepadStateSyncRunner
from src.features.scanning.inventory_commit_service import InventoryCommitService
from src.features.scanning.post_action_evaluator import PostActionEvaluator
from src.features.scanning.post_actions import (
    merge_post_action_config,
    post_actions_enabled,
    summarize_state_changes,
)
from src.features.scanning.scan_parse_coordinator import ScanParseCoordinator
from src.scanner.window_capture import crop_window_border_from_image, game_content_rect
from src.utils.image_io import imread_unicode
from src.utils.logger import logger


def _parse_during_scan_enabled() -> bool:
    import os

    value = os.environ.get("NTE_STREAMING_SCAN_PARSE", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


GRADE_ORDER = ["ACE", "SSS", "SS", "S", "A", "B", "C", "D"]
TRASH_BUTTON_CENTER = (0.89375, 0.21944)
LOCK_BUTTON_CENTER = (0.93047, 0.21944)
STATE_BUTTON_SIZE_RATIO = 0.025


def _state_button_is_active(img: np.ndarray, center: tuple[float, float]) -> bool:
    height, width = img.shape[:2]
    if height < 100 or width < 100:
        return False
    left, top, content_width, content_height = game_content_rect(width, height)
    cx = int(round(left + content_width * center[0]))
    cy = int(round(top + content_height * center[1]))
    size = max(18, int(round(min(content_width, content_height) * STATE_BUTTON_SIZE_RATIO)))
    half = max(1, size // 2)
    roi = img[max(0, cy - half) : min(height, cy + half), max(0, cx - half) : min(width, cx + half)]
    if roi.size == 0:
        return False
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    bright_fraction = float((gray > 95).mean())
    high_value = float(np.percentile(hsv[:, :, 2], 95))
    return bool(bright_fraction > 0.12 and high_value > 130.0)


def _right_panel_button_state_from_image(img: np.ndarray) -> str:
    trash_active = _state_button_is_active(img, TRASH_BUTTON_CENTER)
    lock_active = _state_button_is_active(img, LOCK_BUTTON_CENTER)
    if lock_active:
        return "locked"
    if trash_active:
        return "discarded"
    return "normal"


def _equipment_screenshot_state(image_path: str) -> str:
    try:
        img = imread_unicode(image_path)
        if img is None:
            return "normal"
        img = crop_window_border_from_image(img)
        return _right_panel_button_state_from_image(img)
    except Exception as exc:
        logger.debug(f"装备状态图标检测失败，按普通处理: {image_path} | {exc}")
        return "normal"


def run_streaming_scan_parse(
    scanner,
    processor,
    total_drives: int,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    scan_done_callback: Callable[[int, int], None] | None = None,
    parse_done_callback: Callable[[], None] | None = None,
    post_action_ready_callback: Callable[[], None] | None = None,
    post_actions_config: dict | None = None,
    selected_roles: list[str] | None = None,
    config_dir=None,
    parse_during_scan: bool | None = None,
    low_load_mode: bool = False,
) -> dict:
    """Run gamepad scanning while parsing captured screenshots in a consumer thread."""

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
        state_detector=lambda path: _equipment_screenshot_state(path),
        sleep_fn=time.sleep,
    ).run()
    if parse_done_callback is not None:
        parse_done_callback()

    effective_post_config = post_actions_config
    effective_post_config = merge_post_action_config(effective_post_config) if effective_post_config else None
    if cancel_check is not None and cancel_check():
        logger.warning("流水线扫描已取消，解析结果不会写入库存。")
        post_action_filter_summary = {}
        post_action_summary = summarize_state_changes([])
    elif scan_result.captured_count == int(total_drives):
        evaluation = PostActionEvaluator(
            post_actions_config=effective_post_config,
            selected_roles=selected_roles,
            config_dir=config_dir,
        ).evaluate(scan_result.parsed_items, processor.inventory)
        InventoryCommitService(processor, scanner).commit()
        post_action_summary = GamepadStateSyncRunner(
            scanner,
            total_drives=total_drives,
            post_action_ready_callback=post_action_ready_callback,
            sleep_fn=time.sleep,
        ).sync(evaluation.state_changes, evaluation.config)
        post_action_filter_summary = evaluation.filter_summary
        effective_post_config = evaluation.config
    else:
        post_action_filter_summary = {}
        post_action_summary = summarize_state_changes([])

    stats = scan_result.to_stats()
    if effective_post_config and post_actions_enabled(effective_post_config):
        stats["post_actions_enabled"] = True
        stats.update(post_action_filter_summary)
        stats.update(post_action_summary)
    return stats
