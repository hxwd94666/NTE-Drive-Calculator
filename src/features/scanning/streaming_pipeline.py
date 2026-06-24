# 串联全量扫描截图与后台解析，减少用户总等待时间。
"""Streaming scan/parse pipeline for full gamepad scans."""

from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from src.utils.logger import logger
from src.utils.perf import log_perf


_STOP = object()


def run_streaming_scan_parse(
    scanner,
    processor,
    total_drives: int,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict:
    """Run gamepad scanning while parsing captured screenshots in a consumer thread."""

    captured_queue: queue.Queue = queue.Queue()
    added_paths: list[str] = []
    duplicate_paths: list[str] = []
    failed_paths: list[str] = []
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
                    _item_obj, added = processor.process_image_file(temp_path, filename)
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

    if cancel_check is not None and cancel_check():
        logger.warning("流水线扫描已取消，解析结果不会写入库存。")
    elif captured_count == int(total_drives):
        if getattr(processor, "inventory", None):
            processor._export_to_json()
        scanner._commit_temp_output()

    return {
        "added_paths": added_paths,
        "duplicate_paths": duplicate_paths,
        "failed_paths": failed_paths,
        "success_count": len(added_paths),
        "duplicate_count": len(duplicate_paths),
        "failed_count": len(failed_paths),
        "total_count": captured_count,
        "parse_scope": "full",
    }
