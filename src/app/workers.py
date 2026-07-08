# 定义后台线程工作器，避免耗时任务阻塞界面。
"""Worker thread classes shared by feature controllers."""

from __future__ import annotations

import traceback as tb
import threading
import time

from PySide6.QtCore import QThread, Signal

from src.app import runtime
from src.scanner.drone_scanner import DroneScanner
from src.scanner.batch_processor import BatchProcessor
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

    def __init__(self, target, parent=None):
        super().__init__(parent)
        self.target = target

    def run(self):
        try:
            self.result_ready.emit(self.target())
        except SystemExit as exc:
            logger.error(f"WorkerThread 捕获 SystemExit: {exc}")
            self.error.emit(f"系统异常退出: {exc}")
        except Exception as exc:
            err_detail = f"{exc}\n\n{tb.format_exc()}"
            logger.error(f"WorkerThread 异常: {err_detail}")
            self.error.emit(str(exc))


class ScanWorkerThread(QThread):
    scan_done = Signal(int)
    error = Signal(str)
    scanner_ready = Signal()

    def __init__(self, mode="semi", parent=None):
        super().__init__(parent)
        self.mode = mode
        self.scanner = None

    def run(self):
        try:
            self.scanner = DroneScanner(
                output_dir=str(runtime.SCREENSHOT_DIR),
                template_path=str(runtime.TEMPLATE_DIR / "new_tag.png"),
            )
            self.scanner_ready.emit()
            if self.mode == "auto":
                count = self.scanner.start_scan()
            else:
                count = self.scanner.start_semi_auto_scan()
            self.scan_done.emit(count)
        except Exception as exc:
            logger.error(f"ScanWorker 异常: {exc}")
            self.error.emit(str(exc))


class GamepadScanWorkerThread(QThread):
    scan_done = Signal(int)
    error = Signal(str)
    scanner_ready = Signal()

    def __init__(self, total_drives, parent=None):
        super().__init__(parent)
        self.total_drives = total_drives
        self.scanner = None

    def run(self):
        try:
            from src.scanner.gamepad_controller import GamepadScanner

            self.scanner = GamepadScanner(output_dir=str(runtime.SCREENSHOT_DIR))
            self.scanner_ready.emit()
            count = self.scanner.start_scan(self.total_drives)
            self.scan_done.emit(count)
        except (FileNotFoundError, OSError) as exc:
            logger.error(f"GamepadScanWorker DLL错误: {exc}")
            self.error.emit(
                "ViGEmClient.dll 加载失败，请确认:\n"
                "1. 已安装 ViGEmBus 驱动 (https://github.com/nefarius/ViGEmBus/releases)\n"
                f"2. 重启电脑后再试\n\n原始错误: {exc}"
            )
        except Exception as exc:
            logger.error(f"GamepadScanWorker 异常: {exc}")
            self.error.emit(str(exc))
        finally:
            _close_scanner(self.scanner)


class GamepadScanParseWorkerThread(QThread):
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
        parent=None,
        post_actions_config=None,
        selected_roles=None,
        parse_during_scan=True,
        discrete_gpu_acceleration=False,
        amd_compatibility=False,
    ):
        super().__init__(parent)
        self.total_drives = total_drives
        self.post_actions_config = post_actions_config
        self.selected_roles = list(selected_roles or [])
        self.amd_compatibility = bool(amd_compatibility)
        self.parse_during_scan = bool(parse_during_scan) and not self.amd_compatibility
        self.discrete_gpu_acceleration = bool(discrete_gpu_acceleration) and not self.amd_compatibility
        self.scanner = None
        self._post_actions_ready_event = threading.Event()

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
            from src.features.scanning.streaming_pipeline import run_streaming_scan_parse
            from src.scanner.gamepad_controller import GamepadScanner

            self.scanner = GamepadScanner(output_dir=str(runtime.SCREENSHOT_DIR))
            self.scanner_ready.emit()
            init_start = time.perf_counter()
            processor = BatchProcessor(
                input_dir=str(runtime.SCREENSHOT_DIR),
                output_file=str(runtime.OUTPUT_FILE),
                config_dir=str(runtime.CONFIG_DIR),
                replace_output=True,
                ocr_backend_preference=(
                    "amd_compat"
                    if self.amd_compatibility
                    else ("directml" if self.discrete_gpu_acceleration else "openvino")
                ),
            )
            init_ms = (time.perf_counter() - init_start) * 1000.0
            log_perf(
                logger,
                "vision.processor_init",
                elapsed_ms=init_ms,
                scope="full",
                replace_output=1,
                streaming=int(self.parse_during_scan),
                discrete_gpu=int(self.discrete_gpu_acceleration),
                amd_compat=int(self.amd_compatibility),
            )
            stats = run_streaming_scan_parse(
                self.scanner,
                processor,
                self.total_drives,
                progress_callback=lambda current, total, filename: self.progress.emit(current, total, filename),
                cancel_check=lambda: bool(getattr(self.scanner, "_stopped", False)),
                scan_done_callback=lambda captured, total: self.scan_done.emit(captured, total),
                parse_done_callback=lambda: self.parse_done.emit(),
                post_action_ready_callback=self._notify_post_actions_ready,
                post_actions_config=self.post_actions_config,
                selected_roles=self.selected_roles,
                config_dir=str(runtime.CONFIG_DIR),
                parse_during_scan=self.parse_during_scan,
                low_load_mode=self.amd_compatibility,
            )
            if int(stats.get("total_count", 0) or 0) != int(self.total_drives):
                raise RuntimeError("全量扫描未完整结束，流水线解析结果未写入库存。")
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
                discrete_gpu=int(self.discrete_gpu_acceleration),
                amd_compat=int(self.amd_compatibility),
            )
            self.processing_done.emit(stats)
        except (FileNotFoundError, OSError) as exc:
            logger.error(f"GamepadScanParseWorker DLL错误: {exc}")
            self.error.emit(
                "ViGEmClient.dll 加载失败，请确认:\n"
                "1. 已安装 ViGEmBus 驱动 (https://github.com/nefarius/ViGEmBus/releases)\n"
                f"2. 重启电脑后再试\n\n原始错误: {exc}"
            )
        except Exception as exc:
            err_detail = f"{exc}\n\n{tb.format_exc()}"
            logger.error(f"GamepadScanParseWorker 异常: {err_detail}")
            self.error.emit(str(exc))
        finally:
            _close_scanner(self.scanner)
