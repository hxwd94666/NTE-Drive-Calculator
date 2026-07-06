# 串联全量扫描截图与后台解析，减少用户总等待时间。
"""Streaming scan/parse pipeline for full gamepad scans."""

from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from src.optimizer.scoring import ScoringEngine
from src.features.scanning.post_actions import (
    build_state_changes,
    merge_post_action_config,
    post_actions_enabled,
    summarize_post_action_filtering,
    summarize_state_changes,
)
from src.features.inventory_import.duplicate_filter import RecoverableParseError
from src.scanner.window_capture import crop_window_border_from_image
from src.utils.image_io import imread_unicode
from src.utils.logger import logger
from src.utils.perf import log_perf


_STOP = object()


def _parse_during_scan_enabled() -> bool:
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
    cx = int(round(width * center[0]))
    cy = int(round(height * center[1]))
    size = max(18, int(round(min(width, height) * STATE_BUTTON_SIZE_RATIO)))
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


def _drive_screenshot_is_locked(image_path: str) -> bool:
    return _equipment_screenshot_state(image_path) == "locked"


def _legacy_discard_config(grade: str | None, lock_action: str) -> dict | None:
    if not grade:
        return None
    return {
        "discard": {
            "enabled": True,
            "grade": grade,
            "role_scope": "all",
            "quality_scope": "all",
            "type_scope": "drive",
            "on_locked": "normal" if lock_action == "unlock" else "skip",
            "on_discarded": "normal",
        },
        "lock": {"enabled": False},
    }


def run_streaming_scan_parse(
    scanner,
    processor,
    total_drives: int,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    scan_done_callback: Callable[[int, int], None] | None = None,
    parse_done_callback: Callable[[], None] | None = None,
    post_action_ready_callback: Callable[[], None] | None = None,
    auto_discard_grade: str | None = None,
    auto_discard_lock_action: str = "skip",
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

    captured_queue: queue.Queue | None = queue.Queue() if parse_during_scan else None
    captured_payloads: list[tuple[str, int, int]] = []
    added_paths: list[str] = []
    duplicate_paths: list[str] = []
    failed_paths: list[str] = []
    pending_manual_items: list[dict] = []
    parsed_items: list[tuple[int, object, str]] = []
    parse_error: list[BaseException] = []
    parse_start = time.perf_counter()
    scan_done_emitted = False

    def emit_scan_done(captured: int) -> None:
        nonlocal scan_done_emitted
        if scan_done_callback is not None and not scan_done_emitted:
            scan_done_callback(captured, total_drives)
            scan_done_emitted = True

    def notify_post_action_ready() -> None:
        if post_action_ready_callback is None:
            return
        post_action_ready_callback()
        time.sleep(0.35)

    def final_path_for(filename: str) -> str:
        return str(Path(scanner.output_dir) / filename)

    def process_payload(payload: tuple[str, int, int]) -> None:
        temp_path, index, total = payload
        filename = os.path.basename(temp_path)
        if progress_callback is not None:
            progress_callback(index, total, filename)
        item_start = time.perf_counter()
        try:
            _item_obj, added = processor.process_image_file(
                temp_path,
                filename,
                filter_adjacent_duplicates=False,
            )
            item_ms = (time.perf_counter() - item_start) * 1000.0
            log_perf(
                logger,
                "vision.item",
                elapsed_ms=item_ms,
                index=index,
                total=total,
                filename=filename,
                added=int(bool(added)),
                streaming=int(bool(parse_during_scan)),
            )
            if added:
                added_paths.append(final_path_for(filename))
                if auto_discard_grade and post_actions_config is None:
                    state = "locked" if _drive_screenshot_is_locked(temp_path) else "normal"
                else:
                    state = _equipment_screenshot_state(temp_path)
                if state != "normal":
                    logger.info(f"识别到装备状态: {filename} -> {state}")
                parsed_items.append((index, _item_obj, state))
            else:
                duplicate_paths.append(final_path_for(filename))
                logger.info(f"相邻截图画面与解析数据均一致，按连拍重复过滤: {filename}")
        except RecoverableParseError as exc:
            item_ms = (time.perf_counter() - item_start) * 1000.0
            pending_manual_items.append(exc.to_record(final_path_for(filename), filename))
            log_perf(
                logger,
                "vision.item",
                elapsed_ms=item_ms,
                index=index,
                total=total,
                filename=filename,
                status="pending_manual",
                streaming=int(bool(parse_during_scan)),
            )
            logger.warning(f"解析待补录: {filename} | {exc}")
        except Exception as exc:
            item_ms = (time.perf_counter() - item_start) * 1000.0
            failed_paths.append(final_path_for(filename))
            log_perf(
                logger,
                "vision.item",
                elapsed_ms=item_ms,
                index=index,
                total=total,
                filename=filename,
                status="failed",
                streaming=int(bool(parse_during_scan)),
            )
            logger.error(f"解析失败: {filename} | {exc}")

    def parse_worker() -> None:
        assert captured_queue is not None
        while True:
            payload = captured_queue.get()
            try:
                if payload is _STOP:
                    return
                process_payload(payload)
            except BaseException as exc:
                parse_error.append(exc)
                return
            finally:
                captured_queue.task_done()

    consumer = None
    if parse_during_scan:
        logger.info("全量扫描解析模式: 边扫边解析。")
        consumer = threading.Thread(target=parse_worker, name="NTEStreamingParse", daemon=True)
        consumer.start()
    elif low_load_mode:
        logger.info("全量扫描解析模式: AMD实验性兼容，扫描完成后低负载解析截图。")
    else:
        logger.info("全量扫描解析模式: 低负载，扫描完成后再解析截图。")

    def on_capture(path: str, index: int, total: int) -> None:
        payload = (path, index, total)
        if captured_queue is not None:
            captured_queue.put(payload)
        else:
            captured_payloads.append(payload)

    captured_count = 0
    try:
        captured_count = scanner.start_scan(
            total_drives,
            on_capture=on_capture,
            commit_on_complete=False,
        )
        emit_scan_done(captured_count)
    finally:
        if captured_queue is not None:
            captured_queue.put(_STOP)
            captured_queue.join()
        if consumer is not None:
            consumer.join()

    if not parse_during_scan and captured_count == int(total_drives):
        for payload in captured_payloads:
            process_payload(payload)
            if low_load_mode:
                time.sleep(0.12)

    if parse_error:
        raise RuntimeError(f"流水线解析线程异常: {parse_error[0]}") from parse_error[0]

    parse_ms = (time.perf_counter() - parse_start) * 1000.0
    log_perf(
        logger,
        "vision.batch_parse",
        elapsed_ms=parse_ms,
        scope="full",
        total=captured_count,
        success=len(added_paths),
        duplicate=len(duplicate_paths),
        failed=len(failed_paths),
        pending=len(pending_manual_items),
        avg_ms=(parse_ms / captured_count) if captured_count else 0.0,
        streaming=int(bool(parse_during_scan)),
        low_load=int(bool(low_load_mode)),
    )
    if parse_done_callback is not None:
        parse_done_callback()

    discard_targets = []
    discard_marked = 0
    discard_locked_targets = []
    state_changes = []
    post_action_summary = summarize_state_changes([])
    post_action_filter_summary = {}
    effective_post_config = post_actions_config or _legacy_discard_config(auto_discard_grade, auto_discard_lock_action)
    effective_post_config = merge_post_action_config(effective_post_config) if effective_post_config else None
    if cancel_check is not None and cancel_check():
        logger.warning("流水线扫描已取消，解析结果不会写入库存。")
    elif captured_count == int(total_drives):
        scoring = None
        if effective_post_config and post_actions_enabled(effective_post_config):
            scoring = ScoringEngine(str(config_dir or "config"))
            if scoring.roles_db:
                scoring.evaluate_global_inventory(processor.inventory)
                post_action_filter_summary = summarize_post_action_filtering(
                    parsed_items,
                    effective_post_config,
                )
                state_changes = build_state_changes(
                    parsed_items,
                    effective_post_config,
                    scoring,
                    selected_roles,
                )
                logger.info(
                    f"扫描后管理评估完成: 捕获 {captured_count} 件，"
                    f"成功解析 {len(parsed_items)} 件，"
                    f"参与计算 {post_action_filter_summary.get('post_action_candidate_count', 0)} 件，"
                    f"目标变更 {len(state_changes)} 件。"
                )
                logger.info(
                    "扫描后管理过滤统计: "
                    f"品质范围 {post_action_filter_summary.get('post_action_quality_filtered_count', 0)} 件，"
                    f"处理类别 {post_action_filter_summary.get('post_action_type_filtered_count', 0)} 件，"
                    f"类型范围 {post_action_filter_summary.get('post_action_type_range_filtered_count', 0)} 件。"
                )
                for change in state_changes:
                    logger.info(
                        f"扫描后管理目标: raw_drive_{int(change.get('index', 0)):04d} "
                        f"{change.get('current_state')} -> {change.get('target_state')} "
                        f"quality={change.get('quality')} type={change.get('item_type')}"
                    )
        if getattr(processor, "inventory", None):
            processor._export_to_json()
        scanner._commit_temp_output()
        if state_changes:
            discard_targets = [
                change["index"] for change in state_changes if change.get("target_state") == "discarded"
            ]
            discard_locked_targets = [
                change["index"]
                for change in state_changes
                if change.get("target_state") == "discarded" and change.get("current_state") == "locked"
            ]
        if state_changes and hasattr(scanner, "sync_equipment_states"):
            notify_post_action_ready()
            action_mode = "hmt" if effective_post_config.get("server_region") == "hmt" else "default"
            applied_count = scanner.sync_equipment_states(total_drives, state_changes, action_mode=action_mode)
            post_action_summary = summarize_state_changes(state_changes, applied_count)
            if auto_discard_grade:
                discard_marked = int(post_action_summary.get("discard_set_count", 0))
        elif state_changes:
            logger.warning("扫描后管理目标已生成，但当前扫描器不支持状态同步，已跳过游戏内处理。")
            post_action_summary = summarize_state_changes(state_changes, 0)

    stats = {
        "added_paths": added_paths,
        "duplicate_paths": duplicate_paths,
        "failed_paths": failed_paths,
        "pending_manual_items": pending_manual_items,
        "success_count": len(added_paths),
        "duplicate_count": len(duplicate_paths),
        "failed_count": len(failed_paths),
        "pending_manual_count": len(pending_manual_items),
        "total_count": captured_count,
        "parse_scope": "full",
        "auto_discard_grade": auto_discard_grade,
        "discard_target_count": len(discard_targets),
        "discard_marked_count": discard_marked,
        "discard_locked_target_count": len(discard_locked_targets),
    }
    if effective_post_config and post_actions_enabled(effective_post_config):
        stats["post_actions_enabled"] = True
        stats.update(post_action_filter_summary)
        stats.update(post_action_summary)
    return stats
