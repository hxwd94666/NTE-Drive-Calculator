# 冻结采集组件来源，并将原生采集终止原因转换为安全的玩家提示。
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal


def with_comparison_metadata(
    record: Mapping[str, Any] | None,
    *,
    comparison_id: str | None,
    source: Literal["native", "packet"] | None,
) -> dict[str, Any] | None:
    """Attach Calc ownership outside Core summaries and native hit evidence."""
    if record is None:
        return None
    result = dict(record)
    if comparison_id is not None:
        if not comparison_id.strip() or source not in {"native", "packet"}:
            raise ValueError("双路配对元数据需要有效标识和明确采集来源")
        result["calc_capture"] = {"comparison_id": comparison_id, "source": source}
    return result


def freeze_nte_core_provenance(client: object) -> dict[str, Any]:
    hello = getattr(client, "hello_result", None)
    hello_payload = dict(hello) if isinstance(hello, Mapping) else {}
    executable_sha256 = str(getattr(client, "executable_sha256", None) or "").strip()
    return {
        "core_version": str(hello_payload.get("core_version") or "").strip() or None,
        "protocol_version": hello_payload.get("protocol_version"),
        "data_version": str(hello_payload.get("data_version") or "").strip() or None,
        "executable_sha256": executable_sha256 or None,
    }


def native_capture_end_warning(
    terminal: Mapping[str, Any] | None,
    record: Mapping[str, Any] | None = None,
) -> str:
    reason = native_capture_end_reason(terminal, record)
    if reason is None or reason in {"user_stop", "scene_transition"}:
        return ""
    detail = {
        "source_changed": "游戏采集上下文已失效",
        "queue_overflow": "采集缓冲区不足",
        "peer_disconnected": "采集连接已断开",
        "provider_stopping": "采集组件已停止",
        "start_failed": "采集组件启动失败",
    }.get(reason, "采集组件提前结束")
    evidence = record.get("native_capture") if record else None
    diagnostics = evidence if isinstance(evidence, Mapping) and "reason" in evidence else terminal
    trigger = diagnostics.get("sourceChangeTrigger") if isinstance(diagnostics, Mapping) else None
    trigger_label = {
        "pawn_replication": "出场角色复制通知",
        "pawn_possess": "接管出场角色",
        "pawn_unpossess": "释放出场角色",
        "player_state_replication": "玩家状态复制通知",
        "local_end_play": "本地角色或场景结束",
        "local_context_change": "本地采集上下文变化",
        "game_thread_pulse_exception": "游戏线程采集回调异常",
    }.get(trigger) if isinstance(trigger, str) else None
    if reason == "source_changed" and trigger_label:
        detail += f"（触发：{trigger_label}）"
    return f"增强采集提前结束：{detail}；本场可能缺少逐击数据。"


def native_capture_end_reason(
    terminal: Mapping[str, Any] | None,
    record: Mapping[str, Any] | None = None,
) -> str | None:
    evidence = record.get("native_capture") if record else None
    if isinstance(evidence, Mapping) and "reason" in evidence:
        terminal = evidence
    if terminal is None:
        return None
    reason = terminal.get("reason")
    return str(reason) if reason else None
