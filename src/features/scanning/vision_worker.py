# 在线程中执行截图视觉解析任务。
"""QThread wrapper for screenshot OCR parsing.

The worker reports parse statistics but does not mutate screenshot files after
the parse. File moves/deletes/renames are handled by scan_file_lifecycle after
the worker finishes successfully.
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QThread, Signal

from src.scanner.batch_processor import BatchProcessor
from src.features.inventory_import.duplicate_filter import RecoverableParseError
from src.features.scanning.file_lifecycle import is_allowed_filename
from src.utils.logger import logger
from src.utils.perf import log_perf


INCREMENTAL_PARSE_SCOPES = {"incremental", "incremental_auto", "incremental_semi"}


class VisionWorkerThread(QThread):
    processing_done = Signal(dict)
    canceled = Signal(int)
    error = Signal(str)
    progress = Signal(int, int, str)

    def __init__(
        self,
        input_dir,
        parent=None,
        replace_output=False,
        parse_scope="all",
        skip_names=None,
        config_dir="config",
    ):
        super().__init__(parent)
        self.input_dir = input_dir
        self.replace_output = replace_output
        self.parse_scope = parse_scope
        self.skip_names = set(skip_names or [])
        self.config_dir = config_dir
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True

    def _is_allowed_file(self, filename):
        return is_allowed_filename(filename, self.parse_scope, self.skip_names)

    def run(self):
        worker_start = time.perf_counter()
        try:
            init_start = time.perf_counter()
            processor = BatchProcessor(
                input_dir=self.input_dir,
                config_dir=str(self.config_dir),
                replace_output=self.replace_output,
            )
            init_ms = (time.perf_counter() - init_start) * 1000.0
            log_perf(
                logger,
                "vision.processor_init",
                elapsed_ms=init_ms,
                scope=self.parse_scope,
                replace_output=int(bool(self.replace_output)),
            )
            if not os.path.exists(self.input_dir):
                self.error.emit(f"找不到截图文件夹 {self.input_dir}")
                return

            image_files = [
                f
                for f in os.listdir(self.input_dir)
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
                and os.path.isfile(os.path.join(self.input_dir, f))
            ]
            image_files = [f for f in image_files if self._is_allowed_file(f)]
            image_files.sort()
            total = len(image_files)
            if total == 0:
                self.error.emit("截图文件夹为空，没有需要处理的图片。")
                return

            added_paths = []
            duplicate_paths = []
            failed_paths = []
            pending_manual_items = []
            filter_adjacent_duplicates = self.parse_scope in INCREMENTAL_PARSE_SCOPES
            parse_start = time.perf_counter()
            for idx, filename in enumerate(image_files, 1):
                if self._cancel_requested:
                    break
                self.progress.emit(idx, total, filename)
                file_path = os.path.join(self.input_dir, filename)
                item_start = time.perf_counter()
                try:
                    _item_obj, added = processor.process_image_file(
                        file_path,
                        filename,
                        filter_adjacent_duplicates=filter_adjacent_duplicates,
                    )
                    item_ms = (time.perf_counter() - item_start) * 1000.0
                    log_perf(
                        logger,
                        "vision.item",
                        elapsed_ms=item_ms,
                        index=idx,
                        total=total,
                        filename=filename,
                        added=int(bool(added)),
                    )
                    if not added:
                        duplicate_paths.append(file_path)
                        logger.info(f"增量重复截图已过滤: {filename}")
                    else:
                        added_paths.append(file_path)
                except RecoverableParseError as exc:
                    item_ms = (time.perf_counter() - item_start) * 1000.0
                    pending_manual_items.append(exc.to_record(file_path, filename))
                    log_perf(
                        logger,
                        "vision.item",
                        elapsed_ms=item_ms,
                        index=idx,
                        total=total,
                        filename=filename,
                        status="pending_manual",
                    )
                    logger.warning(f"解析待补录: {filename} | {exc}")
                except Exception as exc:
                    item_ms = (time.perf_counter() - item_start) * 1000.0
                    failed_paths.append(file_path)
                    log_perf(
                        logger,
                        "vision.item",
                        elapsed_ms=item_ms,
                        index=idx,
                        total=total,
                        filename=filename,
                        status="failed",
                    )
                    logger.error(f"解析失败: {filename} | {exc}")

            processed_count = len(processor.inventory)
            parse_ms = (time.perf_counter() - parse_start) * 1000.0
            log_perf(
                logger,
                "vision.batch_parse",
                elapsed_ms=parse_ms,
                scope=self.parse_scope,
                total=total,
                success=len(added_paths),
                duplicate=len(duplicate_paths),
                failed=len(failed_paths),
                pending=len(pending_manual_items),
                avg_ms=(parse_ms / total) if total else 0.0,
            )
            if self._cancel_requested:
                logger.info("VisionWorkerThread: 解析已取消")
                del processor
                self.canceled.emit(processed_count)
                return

            vision_items = [item.model_dump() for item in processor.inventory]
            del processor
            log_perf(
                logger,
                "vision.total",
                elapsed_ms=(time.perf_counter() - worker_start) * 1000.0,
                scope=self.parse_scope,
                total=total,
                success=len(added_paths),
                duplicate=len(duplicate_paths),
                failed=len(failed_paths),
                pending=len(pending_manual_items),
            )
            logger.info("VisionWorkerThread: 即将发射 processing_done 信号")
            self.processing_done.emit(
                {
                    "added_paths": added_paths,
                    "duplicate_paths": duplicate_paths,
                    "failed_paths": failed_paths,
                    "pending_manual_items": pending_manual_items,
                    "success_count": len(added_paths),
                    "duplicate_count": len(duplicate_paths),
                    "failed_count": len(failed_paths),
                    "pending_manual_count": len(pending_manual_items),
                    "total_count": total,
                    "parse_scope": self.parse_scope,
                    "vision_items": vision_items if self.parse_scope in {"full", "all"} else [],
                }
            )
        except SystemExit as exc:
            logger.error(f"VisionWorker 捕获 SystemExit: {exc}")
            self.error.emit(f"系统异常退出: {exc}")
        except Exception as exc:
            import traceback as tb

            err_detail = f"{exc}\n\n{tb.format_exc()}"
            logger.error(f"VisionWorker 异常: {err_detail}")
            self.error.emit(str(exc))
