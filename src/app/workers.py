# 定义后台线程工作器，避免耗时任务阻塞界面。
"""Worker thread classes shared by feature controllers."""

from __future__ import annotations

import traceback as tb

from PySide6.QtCore import QThread, Signal

from src.app import runtime
from src.scanner.drone_scanner import DroneScanner
from src.utils.logger import logger


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
