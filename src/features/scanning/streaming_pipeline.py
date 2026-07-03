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

from src.models.equipment import Drive
from src.optimizer.scoring import ScoringEngine
from src.scanner.window_capture import crop_window_border_from_image
from src.utils.image_io import imread_unicode
from src.utils.logger import logger
from src.utils.perf import log_perf


_STOP = object()
GRADE_ORDER = ["ACE", "SSS", "SS", "S", "A", "B", "C", "D"]


def _lock_icon_mask_is_locked(mask: np.ndarray) -> bool:
    if mask.size == 0:
        return False
    height, width = mask.shape[:2]
    if height < 8 or width < 8:
        return False
    top = mask[: max(1, height // 2)]
    top_left = top[:, : max(1, width // 2)].mean()
    top_right = top[:, width // 2 :].mean()
    return bool(top_left > 0.48 and top_right > 0.48)


def _drive_screenshot_is_locked(image_path: str) -> bool:
    try:
        img = imread_unicode(image_path)
        if img is None:
            return False
        img = crop_window_border_from_image(img)
        height, width = img.shape[:2]
        if height < 100 or width < 100:
            return False

        region = img[
            int(height * 0.06) : int(height * 0.30),
            int(width * 0.55) : int(width * 0.98),
        ]
        if region.size == 0:
            return False

        gray = region.mean(axis=2)
        spread = region.max(axis=2) - region.min(axis=2)
        mask = ((gray > 70) & (spread < 75)).astype("uint8")
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)

        candidate = None
        for label in range(1, count):
            x, y, w, h, area = stats[label]
            if 20 <= area <= 3000 and 8 <= w <= 100 and 8 <= h <= 100 and 0.4 <= (w / h) <= 1.8:
                if candidate is None or x + w > candidate[0]:
                    candidate = (x + w, label, x, y, w, h)

        if candidate is None:
            return False
        _, label, x, y, w, h = candidate
        return _lock_icon_mask_is_locked(labels[y : y + h, x : x + w] == label)
    except Exception as exc:
        logger.debug(f"锁定图标检测失败，按未锁处理: {image_path} | {exc}")
        return False


def _discard_target_indexes(
    processor,
    parsed_items: list[tuple[int, object, bool]],
    grade: str,
    config_dir,
    lock_action: str = "skip",
) -> list[int]:
    if not grade or grade not in GRADE_ORDER or not getattr(processor, "inventory", None):
        return []
    scoring = ScoringEngine(str(config_dir or "config"))
    if not scoring.roles_db:
        return []
    scoring.evaluate_global_inventory(processor.inventory)
    threshold_rank = GRADE_ORDER.index(grade)
    targets = []
    for index, item, locked in parsed_items:
        if locked and lock_action != "unlock":
            continue
        if not isinstance(item, Drive):
            continue
        item_grade = scoring.get_grade_tag(getattr(item, "max_score", 0.0), getattr(item, "area", 1))
        if item_grade in GRADE_ORDER and GRADE_ORDER.index(item_grade) > threshold_rank:
            targets.append(index)
    return targets


def run_streaming_scan_parse(
    scanner,
    processor,
    total_drives: int,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    scan_done_callback: Callable[[int, int], None] | None = None,
    auto_discard_grade: str | None = None,
    auto_discard_lock_action: str = "skip",
    config_dir=None,
) -> dict:
    """Run gamepad scanning while parsing captured screenshots in a consumer thread."""

    captured_queue: queue.Queue = queue.Queue()
    added_paths: list[str] = []
    duplicate_paths: list[str] = []
    failed_paths: list[str] = []
    parsed_items: list[tuple[int, object, bool]] = []
    parse_error: list[BaseException] = []
    parse_start = time.perf_counter()

    def final_path_for(filename: str) -> str:
        return str(Path(scanner.output_dir) / filename)

    def parse_worker() -> None:
        while True:
            payload = captured_queue.get()
            try:
                if payload is _STOP:
                    return
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
                        streaming=1,
                    )
                    if added:
                        added_paths.append(final_path_for(filename))
                        locked = isinstance(_item_obj, Drive) and _drive_screenshot_is_locked(temp_path)
                        if locked:
                            logger.info(f"跳过锁定驱动弃置目标: {filename}")
                        parsed_items.append((index, _item_obj, locked))
                    else:
                        duplicate_paths.append(final_path_for(filename))
                        logger.info(f"相邻截图画面与解析数据均一致，按连拍重复过滤: {filename}")
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
                        streaming=1,
                    )
                    logger.error(f"解析失败: {filename} | {exc}")
            except BaseException as exc:
                parse_error.append(exc)
                return
            finally:
                captured_queue.task_done()

    consumer = threading.Thread(target=parse_worker, name="NTEStreamingParse", daemon=True)
    consumer.start()

    def on_capture(path: str, index: int, total: int) -> None:
        captured_queue.put((path, index, total))

    captured_count = 0
    try:
        captured_count = scanner.start_scan(
            total_drives,
            on_capture=on_capture,
            commit_on_complete=False,
        )
        if scan_done_callback is not None and not auto_discard_grade:
            scan_done_callback(captured_count, total_drives)
    finally:
        captured_queue.put(_STOP)
        captured_queue.join()
        consumer.join()

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
        avg_ms=(parse_ms / captured_count) if captured_count else 0.0,
        streaming=1,
    )

    discard_targets = []
    discard_marked = 0
    discard_locked_targets = []
    if cancel_check is not None and cancel_check():
        logger.warning("流水线扫描已取消，解析结果不会写入库存。")
    elif captured_count == int(total_drives):
        if auto_discard_grade:
            discard_targets = _discard_target_indexes(
                processor,
                parsed_items,
                auto_discard_grade,
                config_dir,
                auto_discard_lock_action,
            )
            locked_indexes = {index for index, _item, locked in parsed_items if locked}
            discard_locked_targets = [index for index in discard_targets if index in locked_indexes]
        if getattr(processor, "inventory", None):
            processor._export_to_json()
        scanner._commit_temp_output()
        if auto_discard_grade:
            if discard_locked_targets:
                discard_marked = scanner.mark_discard_by_indexes(
                    total_drives,
                    discard_targets,
                    locked_indexes=discard_locked_targets,
                )
            else:
                discard_marked = scanner.mark_discard_by_indexes(total_drives, discard_targets)
            if scan_done_callback is not None:
                scan_done_callback(captured_count, total_drives)

    return {
        "added_paths": added_paths,
        "duplicate_paths": duplicate_paths,
        "failed_paths": failed_paths,
        "success_count": len(added_paths),
        "duplicate_count": len(duplicate_paths),
        "failed_count": len(failed_paths),
        "total_count": captured_count,
        "parse_scope": "full",
        "auto_discard_grade": auto_discard_grade,
        "discard_target_count": len(discard_targets),
        "discard_marked_count": discard_marked,
        "discard_locked_target_count": len(discard_locked_targets),
    }
