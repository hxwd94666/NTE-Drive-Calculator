# 为实时战报入口区分工作模式引导与真实连接故障。
from __future__ import annotations

CONNECTION_ERROR_CODES = frozenset({
    "NPCAP_NOT_FOUND", "GAME_PROCESS_NOT_FOUND", "CAPTURE_DEVICE_NOT_FOUND",
    "CAPTURE_START_FAILED", "CAPTURE_FAILED", "PROTOCOL_VERSION_MISMATCH", "HANDSHAKE_REQUIRED",
    "SYSTEM_PROBE_FAILED", "NteCoreNotFoundError",
    "NteCoreProcessError", "NteCoreProtocolError", "NteCoreTimeoutError", "FileNotFoundError",
})


def capture_entry_allowed(controller, *, automatic=False) -> bool:
    policy = controller._work_mode_service
    if policy is not None and (policy.allowed("native_battle") or policy.allowed("packet_capture")):
        if getattr(getattr(policy, "settings", None), "paused", False):
            if not automatic:
                callback = getattr(controller, "_operation_unavailable", None)
                if callback is not None:
                    callback("战报采集", "连接已主动暂停，可在检测区继续连接。", target="detection")
            return False
        return True
    if not automatic:
        callback = getattr(controller, "_operation_entry", None)
        if callback is not None:
            callback("battle_capture", "战报采集")
    return False


def capture_unavailable(controller, detail: str) -> None:
    policy = getattr(controller, "_work_mode_service", None)
    capability = getattr(controller, "_capture_guidance_capability", None)
    if (policy is None or capability is None or not policy.allowed(capability)
            or policy.settings.paused
            or not getattr(controller, "_capture_guidance_enabled", False)
            or getattr(controller, "_capture_unavailable_notified", False)
            or getattr(controller, "_manual_stop_requested", False)
            or getattr(controller, "_closing", False)):
        return
    frozen_context = getattr(controller, "_capture_guidance_context", None)
    if frozen_context is not None and frozen_context != (
        controller._app_context.account.active_account_id, controller._app_context.generation,
    ):
        return
    controller._capture_unavailable_notified = True
    callback = getattr(controller, "_operation_unavailable", None)
    if callback is not None:
        callback("战报采集", detail, target="detection")


def show_capture_connection_failure(controller, state) -> None:
    if state.phase == "error" and state.error_code in CONNECTION_ERROR_CODES:
        capture_unavailable(controller, state.error or state.message)
