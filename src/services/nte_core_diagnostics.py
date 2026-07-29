# 收集并格式化可独立查看的 nte-core 抓包诊断报告。
"""Collect and format a self-contained nte-core capture diagnostic report."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from src.integrations.nte_core import (
    NteCoreClient,
    NteCoreError,
    NteCoreRpcError,
    resolve_nte_core_executable,
)


DiagnosticResult = dict[str, Any]


def collect_nte_core_diagnostics(
    *,
    cwd: str | Path | None = None,
    client_factory: Callable[[Path], NteCoreClient] | None = None,
) -> DiagnosticResult:
    """Query the exact bundled core used by the application without starting capture.

    ``capture.detect`` is deliberately the only capture RPC used here: it probes the
    process/network environment but never creates a capture session or raw packet file.
    """

    result: DiagnosticResult = {
        "executable": "未解析",
        "ok": False,
    }
    client: NteCoreClient | None = None
    try:
        executable = resolve_nte_core_executable()
        result["executable"] = str(executable)
        client = (
            client_factory(executable)
            if client_factory is not None
            else NteCoreClient(executable=executable, cwd=cwd, timeout=10.0)
        )
        client.start()
        result["hello"] = dict(client.hello_result or {})
        detected = client.detect_capture_environment()
        if not isinstance(detected, Mapping):
            raise TypeError("capture.detect 返回了无效结果")
        result["capture_detect"] = dict(detected)
        result["ok"] = True
    except NteCoreError as exc:
        result["error"] = str(exc)
        result["error_type"] = type(exc).__name__
        if isinstance(exc, NteCoreRpcError):
            result["domain_code"] = exc.domain_code
            result["rpc_code"] = exc.code
            result["rpc_data"] = dict(exc.data)
        if client is not None and client.recent_stderr:
            result["stderr"] = list(client.recent_stderr)
    except Exception as exc:
        result["error"] = str(exc)
        result["error_type"] = type(exc).__name__
        if client is not None and client.recent_stderr:
            result["stderr"] = list(client.recent_stderr)
    finally:
        if client is not None:
            client.close()
    return result


def format_nte_core_diagnostics(result: Mapping[str, Any]) -> str:
    """Produce a readable, copyable report while retaining raw core output."""

    lines = ["NTE Drive Calc · nte-core 诊断", f"核心路径：{result.get('executable', '未知')}"]
    hello = result.get("hello")
    if isinstance(hello, Mapping):
        lines.extend(
            [
                f"核心版本：{hello.get('core_version', '未知')}",
                f"协议版本：{hello.get('protocol_version', '未知')}",
                f"数据版本：{hello.get('data_version', '未知')}",
            ]
        )

    detected = result.get("capture_detect")
    if isinstance(detected, Mapping):
        game_found = detected.get("game_process_detected")
        local_ip = detected.get("local_ip_detected")
        devices = detected.get("devices")
        lines.extend(
            [
                "",
                "检测结论",
                f"游戏进程：{'已检测到' if game_found is True else '未检测到' if game_found is False else '核心未提供'}",
                f"游戏网络连接：{'已检测到' if local_ip is True else '未检测到或系统探测失败' if local_ip is False else '核心未提供'}",
                "推荐抓包网卡："
                + _format_compact_value(detected.get("recommended_device")),
                f"Npcap 可用网卡：{len(devices) if isinstance(devices, list) else '未知'} 个",
            ]
        )
        available_devices = capture_device_names(detected)
        if available_devices:
            lines.append("可手动填写的抓取网卡：")
            lines.extend(
                f"  {index}. {name}"
                for index, name in enumerate(available_devices, 1)
            )
            if detected.get("recommended_device") is None:
                lines.append(
                    "未获得自动推荐；请选择其中一项填写到“背包同步 > 抓取网卡”，"
                    "保存设置后重新启动同步。"
                )
    else:
        lines.extend(
            [
                "",
                "检测失败",
                f"错误类型：{result.get('error_type', '未知')}",
                f"错误码：{result.get('domain_code', '无')}",
                f"错误信息：{result.get('error', '未知')}",
            ]
        )

    lines.extend(["", "原始诊断数据", json.dumps(dict(result), ensure_ascii=False, indent=2)])
    return "\n".join(lines)


def _format_compact_value(value: object) -> str:
    if value is None:
        return "无"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def capture_device_names(detected: Mapping[str, Any]) -> list[str]:
    """Return unique capture device names that nte-core can accept manually."""

    devices = detected.get("devices")
    if not isinstance(devices, list):
        return []

    names: list[str] = []
    seen: set[str] = set()
    for device in devices:
        name = (
            device
            if isinstance(device, str)
            else device.get("name")
            if isinstance(device, Mapping)
            else None
        )
        if not isinstance(name, str):
            continue
        name = name.strip()
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return names
