# 协商战报抓包等待能力并确认本次采集实际就绪，取消等待时只收尾不读历史。
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from src.services.battle_capture_lifecycle import start_capture_when_ready, stop_capture_with_timeout


class BattleCaptureStartError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.domain_code = code
        super().__init__(f"战报抓包启动失败（{code}），请重新检测环境。")


def supports_packet_wait(client: Any, source: str) -> bool:
    hello = getattr(client, "hello_result", None)
    return source == "packet" and isinstance(hello, Mapping) and "capture_wait_v1" in hello.get("capabilities", ())


def _operation(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("capture_operation_id", payload.get("operation_id"))
    return str(value) if value else None


def _wait_packet_running(
    client: Any, initial: Mapping[str, Any], *, stop_event: threading.Event,
    require_start: Callable[[], None], on_wait: Callable[[str], None],
) -> bool:
    operation = _operation(initial)
    if operation is None:
        raise BattleCaptureStartError("CAPTURE_OPERATION_ID_MISSING")
    deadline = time.monotonic() + 15.0
    payload = initial
    last_status = None
    while not stop_event.is_set():
        require_start()
        now = time.monotonic()
        actual_operation = _operation(payload)
        if actual_operation is not None and actual_operation != operation:
            raise BattleCaptureStartError("CAPTURE_OPERATION_CHANGED")
        status = payload.get("capture_status", payload.get("status", "starting"))
        if actual_operation != operation:
            status = "starting"
        if status == "failed":
            raise BattleCaptureStartError(str(payload.get("capture_error_code", payload.get("error_code")) or "CAPTURE_START_FAILED"))
        if status == "running":
            return not stop_event.is_set()
        if status == "stopped":
            return False
        if status in {"waiting_game", "waiting_network"}:
            deadline = now + 15.0
        elif status not in {"idle", "starting"}:
            raise BattleCaptureStartError("CAPTURE_STATUS_INVALID")
        elif now >= deadline:
            raise BattleCaptureStartError("CAPTURE_START_TIMEOUT")
        if status != last_status:
            on_wait({
                "waiting_game": "等待启动游戏；战报抓包尚未就绪，可随时停止。",
                "waiting_network": "等待游戏网络连接；战报抓包尚未就绪，可随时停止。",
            }.get(status, "正在初始化战报抓包，等待网卡就绪，可随时停止。"))
            last_status = status
        if stop_event.wait(0.5):
            return False
        payload = client.status()
        if not isinstance(payload, Mapping):
            raise BattleCaptureStartError("CAPTURE_STATUS_INVALID")
    return False


def start_battle_capture(
    client: Any, *, source: str, require_start: Callable[[], None],
    stop_event: threading.Event, on_wait: Callable[[str], None],
    stop_timeout_seconds: float, device_name: str | None = None,
    raw_capture_enabled: bool = False,
    summary_writer: Any = None, capture_operation_id: str | None = None,
) -> bool:
    """Return True only when this capture is ready; clean up cancelled waits."""
    supports_wait = supports_packet_wait(client, source)

    def start_capture():
        require_start()
        result = client.start_capture(
            profile="combat", device_name=device_name, include_incoming=True,
            server_damage_calibration=True,
            raw_capture="enabled" if raw_capture_enabled else "disabled",
            **({"wait_for_game": True} if supports_wait else {}),
        )
        return result

    if not supports_wait:
        return start_capture_when_ready(
            start_capture, stop_event=stop_event,
            wait_for_native_ready=source == "native", on_wait=on_wait,
        )
    if stop_event.is_set():
        return False
    ready = False
    try:
        initial = start_capture()
        if not isinstance(initial, Mapping):
            raise BattleCaptureStartError("CAPTURE_START_RESULT_INVALID")
        ready = _wait_packet_running(
            client, initial, stop_event=stop_event,
            require_start=require_start, on_wait=on_wait,
        )
        return ready
    finally:
        if not ready:
            stop_capture_with_timeout(client, stop_timeout_seconds)
