# 使用采集现有取消事件等待原生场景就绪，并保留原有停止超时边界。
from __future__ import annotations

from collections.abc import Callable, Mapping
import threading
from typing import Any

from src.integrations.nte_core_protocol import (
    NATIVE_CAPTURE_TRANSIENT_REASONS, NteCoreProcessError,
    is_native_capture_not_ready, native_capture_readiness_message,
)


def start_capture_when_ready(
    start_capture: Callable[[], Mapping[str, Any]],
    *,
    stop_event: threading.Event,
    wait_for_native_ready: bool,
    on_wait: Callable[[str], None],
) -> bool:
    """Retry only known transient rejections on the owning capture worker."""
    last_message = None
    while not stop_event.is_set():
        try:
            start_capture()
            return True
        except Exception as error:
            if not is_native_capture_not_ready(error):
                raise
            if not wait_for_native_ready:
                raise
            if stop_event.is_set():
                return False
            message = native_capture_readiness_message(error)
            reason = error.data.get("reason")
            if not isinstance(reason, str) or reason not in NATIVE_CAPTURE_TRANSIENT_REASONS:
                raise NteCoreProcessError(message + "。") from error
            if message != last_message:
                on_wait(message + "，可随时停止采集。")
                last_message = message
            if stop_event.wait(0.5):
                break
    return False


def stop_capture_with_timeout(client: Any, timeout_seconds: float) -> Mapping[str, Any]:
    """Keep the existing bounded stop RPC worker and abort behavior."""
    completed = threading.Event()
    outcome: list[Mapping[str, Any] | Exception] = []

    def stop_capture() -> None:
        try:
            outcome.append(client.stop_capture())
        except Exception as error:
            outcome.append(error)
        finally:
            completed.set()

    threading.Thread(target=stop_capture, name="battle-capture-stop", daemon=True).start()
    if not completed.wait(timeout_seconds):
        abort = getattr(client, "abort", None)
        if callable(abort):
            abort()
        raise RuntimeError(f"nte-core 停止超时（{timeout_seconds:g} 秒）")
    if not outcome:
        raise RuntimeError("nte-core 停止线程未返回结果")
    result = outcome[0]
    if isinstance(result, Exception):
        raise result
    return result
