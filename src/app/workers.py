# 定义后台线程工作器，避免耗时任务阻塞界面。
"""Worker thread classes shared by feature controllers."""

from __future__ import annotations

import traceback as tb
import threading
import time
from concurrent.futures import CancelledError

from PySide6.QtCore import QThread, Signal

from src.integrations.vision.incremental_scan import DroneScanner
from src.integrations.vision.recognition.image_resources import CaptureTemplateFile
from src.integrations.vision.recognition.ocr.backend_runtime import check_cancel
from src.integrations.vision.recognition.batch_processor import BatchProcessor
from src.services.recognition.resources import load_recognition_resources
from src.utils.logger import logger
from src.utils.perf import log_perf


def _close_scanner(scanner):
    if scanner is None or not hasattr(scanner, "close"):
        return
    try:
        scanner.close()
    except Exception as exc:
        logger.warning(f"释放虚拟手柄失败: {exc}")


class WorkerThread(QThread):
    result_ready = Signal(object)
    error = Signal(str)
    progress = Signal(object)

    def __init__(self, target, parent=None):
        super().__init__(parent)
        self.target = target

    def run(self):
        try:
            self.result_ready.emit(self.target())
        except CancelledError:
            self.error.emit("任务已取消")
        except SystemExit as exc:
            logger.error(f"WorkerThread 捕获 SystemExit: {exc}")
            self.error.emit(f"系统异常退出: {exc}")
        except Exception as exc:
            err_detail = f"{exc}\n\n{tb.format_exc()}"
            logger.error(f"WorkerThread 异常: {err_detail}")
            self.error.emit(str(exc))


class ScanWorkerThread(QThread):
    scan_done = Signal(int)
    canceled = Signal()
    error = Signal(str)
    scanner_ready = Signal()

    def __init__(self, *, output_dir, template_path, mode="semi", parent=None, operation_guard=None):
        super().__init__(parent)
        self.operation_guard = operation_guard
        self.output_dir = str(output_dir)
        self.template_path = str(template_path)
        self.mode = mode
        self.scanner = None
        self.capture_template = None
        self.cancel_token = threading.Event()

    def is_cancelled(self):
        return self.cancel_token.is_set() or bool(getattr(self.scanner, '_stopped', False))

    def request_cancel(self):
        self.cancel_token.set()
        if self.scanner is not None:
            self.scanner.emergency_stop()

    def run(self):
        try:
            check_cancel(self.is_cancelled)
            self.scanner = DroneScanner(output_dir=self.output_dir, template_path=self.template_path,
                operation_guard=self.operation_guard, use_new_tag=self.mode == 'auto')
            check_cancel(self.is_cancelled)
            self.capture_template = self.scanner.capture_template
            if self.mode == 'auto' and not isinstance(self.capture_template, CaptureTemplateFile):
                raise ValueError("自动扫描缺少本批标记资源。")
            self.scanner_ready.emit()
            count = self.scanner.start_scan() if self.mode == 'auto' else self.scanner.start_semi_auto_scan()
            check_cancel(self.is_cancelled)
            if self.capture_template is not None:
                self.capture_template.verify()
            check_cancel(self.is_cancelled)
            self.scan_done.emit(count)
        except CancelledError:
            self.cancel_token.set()
            self.canceled.emit()
        except Exception as exc:
            if self.is_cancelled():
                self.cancel_token.set()
                self.canceled.emit()
            else:
                logger.error(f"ScanWorker 异常: {exc}")
                self.error.emit(str(exc))


class FullVisualScanParseWorkerThread(QThread):
    processing_done = Signal(dict)
    error = Signal(str)
    scanner_ready = Signal()
    scan_done = Signal(int, int)
    parse_done = Signal()
    post_actions_ready = Signal()
    progress = Signal(int, int, str)

    def __init__(
        self,
        total_drives,
        *,
        screenshot_dir,
        config_dir,
        user_database_path,
        static_database_path=None,
        parent=None,
        post_actions_config=None,
        selected_roles=None,
        parse_during_scan=True,
        amd_compatibility=False,
        capture_driver="mouse",
        result_is_current=None,
        operation_guard=None,
    ):
        super().__init__(parent)
        self.operation_guard = operation_guard
        self.total_drives = total_drives
        self.screenshot_dir = str(screenshot_dir)
        self.config_dir = str(config_dir)
        self.user_database_path = user_database_path
        self.static_database_path = static_database_path
        self.post_actions_config = post_actions_config
        self.selected_roles = list(selected_roles or [])
        self.amd_compatibility = bool(amd_compatibility)
        self.capture_driver = str(capture_driver or "mouse").strip().casefold()
        if self.capture_driver not in {"mouse", "gamepad"}:
            raise ValueError(f"不支持的全量扫描驱动：{capture_driver}")
        self.mouse_compatibility_mode = (
            self.capture_driver == "mouse" and self.amd_compatibility
        )
        self.mouse_input_profile = (
            "compatibility-low-load-v1"
            if self.mouse_compatibility_mode
            else "standard-low-load-v1"
            if self.capture_driver == "mouse"
            else "one-point-five-trial-v1"
        )
        # Normal mouse scans honor the dual-thread option just like the
        # gamepad route.  Compatibility mode deliberately remains serial and
        # low-load for machines that need the fallback.
        self.capture_queue_maxsize = 7 if self.mouse_compatibility_mode else 21
        self.parse_during_scan = (
            bool(parse_during_scan) and not self.mouse_compatibility_mode
        )
        self.low_load_mode = (
            self.capture_driver == "mouse" and not self.parse_during_scan
        )
        self.low_load_parse_delay_seconds = (
            0.20 if self.mouse_compatibility_mode else 0.12
        )
        self.result_is_current = result_is_current
        self.scanner = None
        self._post_actions_ready_event = threading.Event()
        self._cancel_event = threading.Event()
        self.cancel_token = self._cancel_event

    def request_cancel(self):
        # 停止请求与原生 OCR 使用同一个普通 Event；不把 QObject 闭包传入后端。
        self._cancel_event.set()
        self._post_actions_ready_event.set()
        scanner = self.scanner
        if scanner is not None and hasattr(scanner, "emergency_stop"):
            scanner.emergency_stop()

    def is_cancelled(self):
        return self._cancel_event.is_set() or bool(getattr(self.scanner, "_stopped", False))

    def acknowledge_post_actions_ready(self):
        self._post_actions_ready_event.set()

    def _notify_post_actions_ready(self):
        self._post_actions_ready_event.clear()
        self.post_actions_ready.emit()
        if not self._post_actions_ready_event.wait(timeout=5.0):
            logger.warning("等待扫描后管理前台切换确认超时，将继续执行状态同步。")
        time.sleep(2.0)

    def run(self):
        worker_start = time.perf_counter()
        try:
            check_cancel(self._cancel_event.is_set)
            from src.services.scanning.pipeline import run_streaming_scan_parse
            if self.capture_driver == "mouse":
                from src.integrations.vision.mouse_inventory_scan import MouseInventoryScanner
                from src.integrations.vision.mouse_scan_runtime import require_mouse_scan_runtime

                require_mouse_scan_runtime()
                self.scanner = MouseInventoryScanner(
                    output_dir=self.screenshot_dir,
                    input_speed_profile=self.mouse_input_profile,
                    operation_guard=self.operation_guard,
                )
            else:
                from src.scanner.gamepad_controller import GamepadScanner

                self.scanner = GamepadScanner(output_dir=self.screenshot_dir, operation_guard=self.operation_guard)
            check_cancel(self._cancel_event.is_set)
            self.scanner_ready.emit()
            init_start = time.perf_counter()
            processor = BatchProcessor(
                input_dir=self.screenshot_dir,
                config_dir=self.config_dir,
                resources=load_recognition_resources(self.config_dir, self.static_database_path),
                replace_output=True,
                cancel_check=self._cancel_event.is_set,
                ocr_backend_preference=(
                    "amd_compat"
                    if self.mouse_compatibility_mode
                    else "low_load"
                    if self.low_load_mode
                    else "openvino"
                ),
            )
            check_cancel(self._cancel_event.is_set)
            init_ms = (time.perf_counter() - init_start) * 1000.0
            log_perf(
                logger,
                "vision.processor_init",
                elapsed_ms=init_ms,
                scope="full",
                replace_output=1,
                streaming=int(self.parse_during_scan),
                amd_compat=int(self.amd_compatibility),
            )
            stats = run_streaming_scan_parse(
                self.scanner,
                processor,
                self.total_drives,
                progress_callback=lambda current, total, filename: self.progress.emit(current, total, filename),
                cancel_check=lambda: self._cancel_event.is_set() or bool(getattr(self.scanner, "_stopped", False)),
                scan_done_callback=lambda captured, total: self.scan_done.emit(captured, total),
                parse_done_callback=lambda: self.parse_done.emit(),
                post_action_ready_callback=self._notify_post_actions_ready,
                post_actions_config=self.post_actions_config,
                selected_roles=self.selected_roles,
                config_dir=self.config_dir,
                user_database_path=self.user_database_path,
                static_database_path=self.static_database_path,
                parse_during_scan=self.parse_during_scan,
                low_load_mode=self.low_load_mode,
                low_load_parse_delay_seconds=self.low_load_parse_delay_seconds,
                capture_queue_maxsize=self.capture_queue_maxsize,
                allow_post_actions=True,
                result_is_current=self.result_is_current,
            )
            check_cancel(self._cancel_event.is_set)
            if stats.get("discarded_stale"):
                self.processing_done.emit(stats)
                return
            if int(stats.get("total_count", 0) or 0) != int(self.total_drives):
                raise RuntimeError("全量扫描未完整结束，流水线解析结果未写入库存。")
            from src.integrations.vision.capture_files import freeze_capture_stage
            stats["capture_stage"] = freeze_capture_stage(
                self.scanner.output_dir, self.scanner.capture_dir, int(self.total_drives))
            stats["capture_cancel_token"] = self.cancel_token
            stats["recognition_resources"] = processor.resources
            stats["vision_items"] = [item.model_dump() for item in processor.inventory]
            stats["capture_driver"] = self.capture_driver
            if self.capture_driver == "mouse":
                stats["mouse_input_profile"] = self.mouse_input_profile
            del processor
            log_perf(
                logger,
                "vision.total",
                elapsed_ms=(time.perf_counter() - worker_start) * 1000.0,
                scope="full",
                total=stats.get("total_count", 0),
                success=stats.get("success_count", 0),
                duplicate=stats.get("duplicate_count", 0),
                failed=stats.get("failed_count", 0),
                streaming=int(self.parse_during_scan),
                amd_compat=int(self.amd_compatibility),
            )
            check_cancel(self._cancel_event.is_set)
            self.processing_done.emit(stats)
        except CancelledError:
            self.error.emit("扫描已取消")
        except (FileNotFoundError, OSError) as exc:
            logger.error(f"FullVisualScanParseWorker 系统依赖错误: {exc}")
            if self.capture_driver == "gamepad":
                self.error.emit(
                    "ViGEmClient.dll 加载失败，请确认:\n"
                    "1. 已安装 ViGEmBus 驱动 (https://github.com/nefarius/ViGEmBus/releases)\n"
                    f"2. 重启电脑后再试\n\n原始错误: {exc}"
                )
            else:
                self.error.emit(str(exc))
        except Exception as exc:
            err_detail = f"{exc}\n\n{tb.format_exc()}"
            logger.error(f"FullVisualScanParseWorker 异常: {err_detail}")
            self.error.emit(str(exc))
        finally:
            _close_scanner(self.scanner)


class GamepadScanParseWorkerThread(FullVisualScanParseWorkerThread):
    """Compatibility wrapper preserving the legacy gamepad default."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("capture_driver", "gamepad")
        super().__init__(*args, **kwargs)
