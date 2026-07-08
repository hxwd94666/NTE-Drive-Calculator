# 协调全量扫描截图捕获与解析流程。
"""Coordinate full-scan capture and screenshot parsing."""

from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from src.features.inventory_import.duplicate_filter import RecoverableParseError
from src.utils.logger import logger
from src.utils.perf import log_perf


_STOP = object()


@dataclass
class ScanParseResult:
    added_paths: list[str] = field(default_factory=list)
    duplicate_paths: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)
    pending_manual_items: list[dict] = field(default_factory=list)
    parsed_items: list[tuple[int, object, str]] = field(default_factory=list)
    captured_count: int = 0
    parse_ms: float = 0.0
    parse_during_scan: bool = False
    low_load_mode: bool = False

    def to_stats(self) -> dict:
        return {
            "added_paths": self.added_paths,
            "duplicate_paths": self.duplicate_paths,
            "failed_paths": self.failed_paths,
            "pending_manual_items": self.pending_manual_items,
            "success_count": len(self.added_paths),
            "duplicate_count": len(self.duplicate_paths),
            "failed_count": len(self.failed_paths),
            "pending_manual_count": len(self.pending_manual_items),
            "total_count": self.captured_count,
            "parse_scope": "full",
        }


class ScanParseCoordinator:
    """Runs screenshot capture and image parsing without owning persistence."""

    def __init__(
        self,
        scanner,
        processor,
        total_drives: int,
        *,
        progress_callback: Callable[[int, int, str], None] | None = None,
        scan_done_callback: Callable[[int, int], None] | None = None,
        parse_during_scan: bool = False,
        low_load_mode: bool = False,
        state_detector: Callable[[str], str] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.scanner = scanner
        self.processor = processor
        self.total_drives = int(total_drives)
        self.progress_callback = progress_callback
        self.scan_done_callback = scan_done_callback
        self.parse_during_scan = bool(parse_during_scan) and not bool(low_load_mode)
        self.low_load_mode = bool(low_load_mode)
        self.state_detector = state_detector or (lambda _path: "normal")
        self.sleep_fn = sleep_fn
        self._scan_done_emitted = False

    def run(self) -> ScanParseResult:
        result = ScanParseResult(
            parse_during_scan=self.parse_during_scan,
            low_load_mode=self.low_load_mode,
        )
        captured_queue: queue.Queue | None = queue.Queue() if self.parse_during_scan else None
        captured_payloads: list[tuple[str, int, int]] = []
        parse_errors: list[BaseException] = []
        parse_start = time.perf_counter()

        if self.parse_during_scan:
            logger.info("全量扫描解析模式: 边扫边解析。")
        elif self.low_load_mode:
            logger.info("全量扫描解析模式: AMD实验性兼容，扫描完成后低负载解析截图。")
        else:
            logger.info("全量扫描解析模式: 低负载，扫描完成后再解析截图。")

        def parse_worker() -> None:
            assert captured_queue is not None
            while True:
                payload = captured_queue.get()
                try:
                    if payload is _STOP:
                        return
                    self._process_payload(payload, result)
                except BaseException as exc:
                    parse_errors.append(exc)
                    return
                finally:
                    captured_queue.task_done()

        consumer = None
        if self.parse_during_scan:
            consumer = threading.Thread(target=parse_worker, name="NTEStreamingParse", daemon=True)
            consumer.start()

        def on_capture(path: str, index: int, total: int) -> None:
            payload = (path, index, total)
            if captured_queue is not None:
                captured_queue.put(payload)
            else:
                captured_payloads.append(payload)

        try:
            result.captured_count = self.scanner.start_scan(
                self.total_drives,
                on_capture=on_capture,
                commit_on_complete=False,
            )
            self._emit_scan_done(result.captured_count)
        finally:
            if captured_queue is not None:
                captured_queue.put(_STOP)
                captured_queue.join()
            if consumer is not None:
                consumer.join()

        if not self.parse_during_scan and result.captured_count == self.total_drives:
            for payload in captured_payloads:
                self._process_payload(payload, result)
                if self.low_load_mode:
                    self.sleep_fn(0.12)

        if parse_errors:
            raise RuntimeError(f"流水线解析线程异常: {parse_errors[0]}") from parse_errors[0]

        result.parse_ms = (time.perf_counter() - parse_start) * 1000.0
        self._log_batch(result)
        return result

    def _emit_scan_done(self, captured: int) -> None:
        if self.scan_done_callback is not None and not self._scan_done_emitted:
            self.scan_done_callback(captured, self.total_drives)
            self._scan_done_emitted = True

    def _final_path_for(self, filename: str) -> str:
        return str(Path(self.scanner.output_dir) / filename)

    def _process_payload(self, payload: tuple[str, int, int], result: ScanParseResult) -> None:
        temp_path, index, total = payload
        filename = os.path.basename(temp_path)
        if self.progress_callback is not None:
            self.progress_callback(index, total, filename)
        item_start = time.perf_counter()
        try:
            item_obj, added = self.processor.process_image_file(
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
                streaming=int(bool(self.parse_during_scan)),
            )
            if added:
                result.added_paths.append(self._final_path_for(filename))
                state = self.state_detector(temp_path)
                if state != "normal":
                    logger.info(f"识别到装备状态: {filename} -> {state}")
                result.parsed_items.append((index, item_obj, state))
            else:
                result.duplicate_paths.append(self._final_path_for(filename))
                logger.info(f"相邻截图画面与解析数据均一致，按连拍重复过滤: {filename}")
        except RecoverableParseError as exc:
            item_ms = (time.perf_counter() - item_start) * 1000.0
            result.pending_manual_items.append(exc.to_record(self._final_path_for(filename), filename))
            log_perf(
                logger,
                "vision.item",
                elapsed_ms=item_ms,
                index=index,
                total=total,
                filename=filename,
                status="pending_manual",
                streaming=int(bool(self.parse_during_scan)),
            )
            logger.warning(f"解析待补录: {filename} | {exc}")
        except Exception as exc:
            item_ms = (time.perf_counter() - item_start) * 1000.0
            result.failed_paths.append(self._final_path_for(filename))
            log_perf(
                logger,
                "vision.item",
                elapsed_ms=item_ms,
                index=index,
                total=total,
                filename=filename,
                status="failed",
                streaming=int(bool(self.parse_during_scan)),
            )
            logger.error(f"解析失败: {filename} | {exc}")

    def _log_batch(self, result: ScanParseResult) -> None:
        log_perf(
            logger,
            "vision.batch_parse",
            elapsed_ms=result.parse_ms,
            scope="full",
            total=result.captured_count,
            success=len(result.added_paths),
            duplicate=len(result.duplicate_paths),
            failed=len(result.failed_paths),
            pending=len(result.pending_manual_items),
            avg_ms=(result.parse_ms / result.captured_count) if result.captured_count else 0.0,
            streaming=int(bool(result.parse_during_scan)),
            low_load=int(bool(result.low_load_mode)),
        )
