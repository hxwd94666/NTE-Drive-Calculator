# 持有一次工坊启动刷新：有主窗口父级的延时器、单线程和取消后的物理收尾。
from __future__ import annotations

from pathlib import Path
from threading import Thread

from PySide6.QtCore import QObject, QTimer

from src.services.characters.workshop_weight_template_service import WorkshopRefreshToken, start_workshop_weight_template_refresh


class WorkshopRefreshController(QObject):
    """本机公共模板不属账号；窗口仅启动一次，退出等现有线程结束而不重发。"""

    def __init__(self, template_file: Path, static_database_path: Path, *, parent: QObject) -> None:
        super().__init__(parent)
        self._template_file = template_file
        self._static_database_path = static_database_path
        self._cancel = WorkshopRefreshToken()
        self._worker: Thread | None = None
        self._started = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh)

    def start(self, delay_ms: int = 1500) -> None:
        if self._started or self._cancel.is_set():
            return
        self._started = True
        self._timer.start(delay_ms)

    def _refresh(self) -> None:
        if not self._cancel.is_set():
            self._worker = start_workshop_weight_template_refresh(
                self._template_file, self._static_database_path, cancel_token=self._cancel,
            )

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def close(self) -> None:
        # GUI 不 join 网络线程；主窗口复用已有非阻塞退场，等 is_running 为假才销毁。
        self._timer.stop()
        self._cancel.set()
