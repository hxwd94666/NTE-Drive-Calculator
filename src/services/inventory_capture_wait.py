# 处理抓包等待能力、操作代次以及同步写入前的授权复核。
from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any

from src.integrations.operation_guard import require_operation


class InventorySyncCancelled(Exception):
    pass


class CaptureStartError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.domain_code = code
        super().__init__(f"采集初始化失败（{code}），请重新检测环境。")


def require_inventory_operation(service: Any, capability: str | None = None) -> None:
    if service._stop_requested.is_set() or (
        service._context_is_current is not None and not service._context_is_current()
    ):
        raise InventorySyncCancelled()
    if capability is None:
        capability = "native_sync" if service.capture_source == "native" else "packet_capture"
    require_operation(service._operation_guard, capability)


class CaptureWaitMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.status = "starting"
        self.operation_id: str | None = None
        self.error_code: str | None = None

    def update(self, payload: Mapping[str, Any]) -> None:
        operation = payload.get("capture_operation_id", payload.get("operation_id"))
        status = payload.get("capture_status", payload.get("status"))
        with self._lock:
            if operation and self.operation_id and str(operation) != self.operation_id:
                return
            if operation:
                self.operation_id = str(operation)
            if status in {"idle", "waiting_game", "waiting_network", "starting", "running", "failed", "stopped"}:
                self.status = str(status)
            code = payload.get("capture_error_code", payload.get("error_code"))
            if code:
                self.error_code = str(code)

    def read(self) -> tuple[str, str | None]:
        with self._lock:
            return self.status, self.error_code


def receive_capture_status(service: Any, event: Mapping[str, Any]) -> None:
    payload = event.get("params") if event.get("method") == "event.capture.status" else event
    if not isinstance(payload, Mapping) or payload.get("profile") != "inventory":
        return
    if service._stop_requested.is_set():
        return
    service._capture_monitor.update(payload)
    if service._capture_monitor.read()[0] == "running":
        service._capture_ready.set()


def wait_capture_ready(
    service: Any, client: Any, *, supports_wait: bool, diagnostics: bool = False,
) -> bool:
    deadline = time.monotonic() + 15.0
    next_status = 0.0
    last_state = ""
    while not service._stop_requested.is_set():
        require_inventory_operation(service)
        if diagnostics:
            require_inventory_operation(service, "diagnostics")
        now = time.monotonic()
        if supports_wait and now >= next_status:
            service._capture_monitor.update(client.status())
            next_status = now + 0.5
        status, error_code = service._capture_monitor.read()
        if status == "failed":
            raise CaptureStartError(error_code or "CAPTURE_START_FAILED")
        if status == "running" or service._capture_ready.is_set():
            return True
        normal_wait = supports_wait and status in {"waiting_game", "waiting_network"}
        if normal_wait:
            deadline = now + 15.0
        elif now >= deadline:
            raise TimeoutError("nte-core 抓包初始化超时，未进入 running 状态")
        if status != last_state:
            message = {
                "waiting_game": "等待启动游戏；抓包尚未就绪。",
                "waiting_network": "等待游戏网络连接；抓包尚未就绪。",
            }.get(status, "正在初始化抓包，等待网卡就绪")
            service._publish("waiting" if normal_wait else "starting", message, running=True, capturing=False)
            last_state = status
        service._stop_requested.wait(service._poll_seconds)
    return False
