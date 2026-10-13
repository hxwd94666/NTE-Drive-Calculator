# 在线程中执行截图视觉解析任务。
"""QThread wrapper for screenshot OCR parsing.

The worker reports parse statistics but does not mutate screenshot files after
the parse. File moves/deletes/renames are handled by scan_file_lifecycle after
the worker finishes successfully.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import CancelledError
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from src.integrations.bundled_resources import bundled_config_dir
from src.integrations.vision.capture_files import CaptureStage, check_capture_stage
from src.integrations.vision.recognition.resources import RecognitionResources
from src.integrations.vision.recognition.backend_runtime import check_cancel
from src.integrations.vision.recognition.model_resources import OcrModelResourceError
from src.integrations.vision.recognition.batch_processor import BatchProcessor
from src.services.recognition.resources import load_recognition_resources, check_recognition_resources
from src.integrations.vision.recognition.duplicate_filter import RecoverableParseError, filename_sequence_key
from src.features.scanning.file_lifecycle import ScanFileLifecycle, is_allowed_filename
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
        config_dir: str | Path | None = None,
        static_database_path: str | Path | None = None,
        capture_template=None, capture_cancel_token: threading.Event | None = None,
        capture_stage: CaptureStage | None = None, recognition_resources: RecognitionResources | None = None,
        capture_driver: str | None = None,
    ):
        super().__init__(parent)
        if capture_stage is not None and (capture_driver not in {"mouse", "gamepad"}
                or parse_scope != "full" or not replace_output
                or not isinstance(recognition_resources, RecognitionResources)):
            raise ValueError("继续暂存必须使用本次完整截图与冻结识别资料。")
        self.capture_stage = capture_stage
        self.capture_driver = capture_driver
        self.recognition_resources = recognition_resources
        self.input_dir = str(capture_stage.directory) if capture_stage is not None else input_dir
        self.replace_output = replace_output
        self.parse_scope = parse_scope
        self.skip_names = set(skip_names or [])
        self.config_dir = Path(config_dir) if config_dir is not None else bundled_config_dir()
        self.static_database_path = static_database_path
        self._cancel_event = threading.Event()
        self.capture_template = capture_template
        self.capture_cancel_token = capture_cancel_token
        parse_token = self._cancel_event
        self._cancel_check = (parse_token.is_set if capture_cancel_token is None else
                              lambda: parse_token.is_set() or capture_cancel_token.is_set())

    def is_cancelled(self):
        return self._cancel_check()

    def request_cancel(self):
        self._cancel_event.set()

    def _is_allowed_file(self, filename):
        return is_allowed_filename(filename, self.parse_scope, self.skip_names)

    def run(self):
        worker_start = time.perf_counter()
        processed_count = 0
        processor = None
        try:
            check_cancel(self._cancel_check)
            if self.capture_stage is not None:
                check_capture_stage(self.capture_stage)
                check_recognition_resources(self.recognition_resources)
                check_cancel(self._cancel_check)
            init_start = time.perf_counter()
            processor = BatchProcessor(
                input_dir=self.input_dir,
                config_dir=str(self.config_dir),
                resources=self.recognition_resources or load_recognition_resources(self.config_dir, self.static_database_path),
                replace_output=self.replace_output,
                cancel_check=self._cancel_check, capture_template=self.capture_template,
            )
            check_cancel(self._cancel_check)
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

            if self.capture_stage is not None and processor.resources != self.recognition_resources:
                raise ValueError("继续暂存的 OCR、布局或识别资料身份发生变化。")
            image_files = [
                f
                for f in os.listdir(self.input_dir)
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
                and os.path.isfile(os.path.join(self.input_dir, f))
            ]
            if self.capture_stage is not None:
                # 只读这个 owner 冻结的正式文件集合；不递归寻找任何旧 stage。
                image_files = [item.name for item in self.capture_stage.files]
            else:
                image_files = [f for f in image_files if self._is_allowed_file(f)]
            image_files.sort()
            total = len(image_files)
            if total == 0:
                self.error.emit("截图文件夹为空，没有需要处理的图片。")
                return

            # 复用本批已初始化的解析器；资源/取消故障直接终止，不当作普通比对失败。
            preparation = ScanFileLifecycle(Path(self.input_dir)).prepare_incremental_parse(self.parse_scope, processor)
            check_cancel(self._cancel_check)
            if preparation.baseline_missing:
                self.error.emit("增量基准截图缺失或损坏，请先重新全量扫描；原截图保留。")
                return
            self.skip_names.update(preparation.skip_names)
            image_files = [f for f in image_files if self._is_allowed_file(f)]
            total = len(image_files)
            added_paths = []
            duplicate_paths = []
            failed_paths = []
            pending_manual_items = []
            scan_number_by_uid = {}
            filter_adjacent_duplicates = self.parse_scope in INCREMENTAL_PARSE_SCOPES
            parse_start = time.perf_counter()
            for idx, filename in enumerate(image_files, 1):
                if self._cancel_check():
                    break
                self.progress.emit(idx, total, filename)
                file_path = os.path.join(self.input_dir, filename)
                sequence_key = filename_sequence_key(filename)
                scan_number = sequence_key[1] if sequence_key else idx
                item_start = time.perf_counter()
                try:
                    _item_obj, added = processor.process_image_file(
                        file_path,
                        filename,
                        filter_adjacent_duplicates=filter_adjacent_duplicates,
                    )
                    check_cancel(self._cancel_check)
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
                        scan_number_by_uid[str(getattr(_item_obj, "uid", ""))] = scan_number
                except (CancelledError, OcrModelResourceError):
                    raise
                except RecoverableParseError as exc:
                    item_ms = (time.perf_counter() - item_start) * 1000.0
                    record = exc.to_record(file_path, filename)
                    record["item"]["_scan_number"] = scan_number
                    pending_manual_items.append(record)
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
            if self._cancel_check():
                logger.info("VisionWorkerThread: 解析已取消")
                processor = None
                self.canceled.emit(processed_count)
                return

            vision_items = []
            for item in processor.inventory:
                data = item.model_dump()
                data["_scan_number"] = scan_number_by_uid.get(str(item.uid))
                vision_items.append(data)
            resources = processor.resources
            if self.capture_template is not None or self.capture_stage is not None:
                check_recognition_resources(resources)
            if self.capture_stage is not None:
                check_capture_stage(self.capture_stage)
            check_cancel(self._cancel_check)
            processor = None
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
            check_cancel(self._cancel_check)
            self.processing_done.emit(
                {
                    "recognition_resources": resources,
                    "capture_stage": self.capture_stage,
                    "capture_driver": self.capture_driver,
                    "capture_cancel_token": self.capture_cancel_token,
                    "delete_after_parse": list(preparation.delete_after_parse),
                    "probe_duplicate_count": preparation.probe_duplicate_count,
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
        except CancelledError:
            if processor is not None:
                processed_count = len(processor.inventory)
            self.canceled.emit(processed_count)
        except SystemExit as exc:
            if self._cancel_check():
                self.canceled.emit(processed_count)
                return
            logger.error(f"VisionWorker 捕获 SystemExit: {exc}")
            self.error.emit(f"系统异常退出: {exc}")
        except Exception as exc:
            if self._cancel_check():
                self.canceled.emit(processed_count)
                return
            import traceback as tb

            err_detail = f"{exc}\n\n{tb.format_exc()}"
            logger.error(f"VisionWorker 异常: {err_detail}")
            self.error.emit(str(exc))
