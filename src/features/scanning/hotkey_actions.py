# 将全局热键动作投影为当前扫描 worker 的停止、截图和完成标记。
"""Scanning-specific actions invoked by the shared global hotkey manager."""

from __future__ import annotations

from src.utils.logger import logger


def on_hotkey_stop(owner) -> None:
    # 已结束的旧 worker 仍可能被 owner 持有；按实际运行态选取，而非对象真值。
    worker = next((candidate for candidate in (
        getattr(owner, '_scan_worker', None), getattr(owner, '_gamepad_worker', None),
    ) if candidate is not None and candidate.isRunning()), None)
    if worker is None:
        logger.warning('收到停止热键 {}，但当前没有运行中的扫描任务。',
                       owner._hotkey_manager.configuration.stop)
        return
    logger.warning('收到停止热键 {}，准备停止当前扫描/状态同步任务。',
                   owner._hotkey_manager.configuration.stop)
    if hasattr(worker, 'request_cancel'):
        worker.request_cancel()
    scanner = worker.scanner
    if scanner is None:
        return
    if not hasattr(worker, 'request_cancel'):
        if hasattr(scanner, 'emergency_stop'):
            scanner.emergency_stop()
        else:
            scanner._stopped = True
    scanner._finish_flag = True


def on_hotkey_capture(owner) -> None:
    worker = getattr(owner, "_scan_worker", None)
    if worker and worker.scanner:
        worker.scanner._capture_flag = True


def on_hotkey_finish(owner) -> None:
    worker = getattr(owner, "_scan_worker", None)
    if worker and worker.scanner:
        worker.scanner._finish_flag = True
