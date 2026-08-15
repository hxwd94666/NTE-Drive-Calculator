# 收集并格式化可独立查看的 nte-core 抓包诊断报告。
"""Collect a compact, actionable nte-core capture diagnostic report."""

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
from src.integrations.windows_capture_diagnostics import collect_windows_capture_support


DiagnosticResult = dict[str, Any]


def collect_nte_core_diagnostics(
    *,
    cwd: str | Path | None = None,
    client_factory: Callable[[Path], NteCoreClient] | None = None,
) -> DiagnosticResult:
    """Inspect the bundled core and Windows capture prerequisites without capture.

    ``capture.detect`` is the sole core RPC. Windows support facts are read-only and
    contain no addresses, MACs, endpoints or network-configuration changes.
    """

    result: DiagnosticResult = {"ok": False}
    client: NteCoreClient | None = None
    try:
        executable = resolve_nte_core_executable()
        client = (
            client_factory(executable)
            if client_factory is not None
            else NteCoreClient(executable=executable, cwd=cwd, timeout=10.0)
        )
        client.start()
        hello = client.hello_result or {}
        result["core"] = {
            "version": hello.get("core_version"),
            "protocol_version": hello.get("protocol_version"),
        }
        result["windows_capture_support"] = collect_windows_capture_support(
            core_executable=executable
        )
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
            result["rpc_data"] = _compact_rpc_data(exc.data)
        _copy_recent_stderr(result, client)
    except Exception as exc:
        result["error"] = str(exc)
        result["error_type"] = type(exc).__name__
        _copy_recent_stderr(result, client)
    finally:
        if client is not None:
            client.close()
    return result


def format_nte_core_diagnostics(result: Mapping[str, Any]) -> str:
    """Produce a copyable support report containing only actionable capture facts."""

    lines = ["NTE Drive Calc · nte-core 抓包诊断"]
    core = result.get("core")
    if isinstance(core, Mapping):
        lines.append(
            "核心："
            f"v{core.get('version', '未知')}（协议 {core.get('protocol_version', '未知')}）"
        )

    detected = result.get("capture_detect")
    if not isinstance(detected, Mapping):
        lines.extend(_format_core_error(result))
        return "\n".join(lines)

    support = result.get("windows_capture_support")
    lines.extend(_format_capture_summary(detected))
    if isinstance(support, Mapping):
        lines.extend(_format_windows_support(support))
    lines.extend(_format_next_step(detected, support if isinstance(support, Mapping) else {}))
    return "\n".join(lines)


def capture_device_names(detected: Mapping[str, Any]) -> list[str]:
    """Return unique capture device names that nte-core can accept manually."""

    devices = detected.get("devices")
    if not isinstance(devices, list):
        return []

    names: list[str] = []
    seen: set[str] = set()
    for device in devices:
        name = device if isinstance(device, str) else device.get("name") if isinstance(device, Mapping) else None
        if not isinstance(name, str):
            continue
        name = name.strip()
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def _copy_recent_stderr(result: DiagnosticResult, client: NteCoreClient | None) -> None:
    if client is not None and client.recent_stderr:
        result["stderr"] = list(client.recent_stderr[-3:])


def _format_capture_summary(detected: Mapping[str, Any]) -> list[str]:
    devices = capture_device_names(detected)
    lines = [
        "",
        "抓包枚举",
        f"游戏进程：{_yes_no_unknown(detected.get('game_process_detected'))}",
        f"游戏网络连接：{_yes_no_unknown(detected.get('local_ip_detected'))}",
        f"Npcap 枚举设备：{len(devices)} 个",
        f"自动选择：{_format_compact_value(detected.get('recommended_device'))}",
    ]
    if devices:
        lines.append("可手动选择：设置页提供“选择可用网卡”，无需复制设备内部名称。")
    else:
        lines.extend(
            [
                "核心枚举错误详情：协议未提供（核心仅返回零设备）。",
                "逐网卡过滤原因：协议未提供。",
            ]
        )
    return lines


def _format_windows_support(support: Mapping[str, Any]) -> list[str]:
    if support.get("supported") is False:
        return ["", "Windows 抓包环境", str(support.get("reason", "当前系统未提供 Windows 探测"))]
    installation = support.get("npcap_installation")
    service = support.get("driver_service")
    adapters = support.get("network_adapters")
    libraries = support.get("runtime_libraries")
    lines = ["", "Windows 抓包环境"]
    if isinstance(installation, Mapping):
        installed = bool(installation.get("directory_present"))
        tool_ready = bool(installation.get("installer_tool_present"))
        lines.append("Npcap 安装：" + ("已检测到" if installed and tool_ready else "目录或驱动工具不完整"))
        if installation.get("driver_log_present"):
            lines.append("Npcap 驱动日志：可用（NPFInstall.log）")
    if isinstance(service, Mapping):
        lines.append("Npcap 驱动服务：" + _service_text(service))
    if isinstance(adapters, Mapping):
        lines.extend(_adapter_lines(adapters))
    if isinstance(libraries, list):
        lines.append("抓包 DLL 候选：" + _library_text(libraries))
    return lines


def _format_next_step(detected: Mapping[str, Any], support: Mapping[str, Any]) -> list[str]:
    devices = capture_device_names(detected)
    if devices:
        if detected.get("recommended_device") is None:
            return [
                "",
                "下一步",
                "未获得自动推荐；在“背包同步 > 抓取网卡”选择其中一项并保存后重试。",
            ]
        return ["", "下一步", "抓包环境已满足枚举条件；若同步仍失败，请提交同步阶段错误。"]

    installation = support.get("npcap_installation")
    service = support.get("driver_service")
    adapters = support.get("network_adapters")
    if isinstance(installation, Mapping) and not installation.get("installer_tool_present"):
        action = "Npcap 安装不完整：以管理员身份修复或重装 Npcap，完成后重启 Windows。"
    elif isinstance(service, Mapping) and service.get("state") == "missing":
        action = "未发现 Npcap 驱动服务：修复或重装 Npcap，完成后重启 Windows。"
    elif isinstance(service, Mapping) and _service_state(service).casefold() != "running":
        action = "Npcap 驱动未运行：先检查服务错误与 NPFInstall.log，再修复或重装 Npcap。"
    elif isinstance(adapters, Mapping) and adapters.get("state") == "ok" and not adapters.get("active_count"):
        action = "Windows 未检测到已启用的网络适配器：连接并启用实际联网的 Wi-Fi 或以太网后重试。"
    else:
        action = (
            "Windows 已有 Npcap/网卡线索，但核心枚举为 0：检查 VPN、加速器、虚拟网卡和终端防护的网络过滤驱动；"
            "提交本报告及 NPFInstall.log。"
        )
    return ["", "下一步", action]


def _format_core_error(result: Mapping[str, Any]) -> list[str]:
    lines = [
        "",
        "核心调用失败",
        f"错误类别：{result.get('error_type', '未知')}",
        f"错误码：{result.get('domain_code') or result.get('rpc_code') or '未提供'}",
        f"信息：{result.get('error', '未知')}",
    ]
    stderr = result.get("stderr")
    if isinstance(stderr, list) and stderr:
        lines.append("核心输出：" + " | ".join(str(item) for item in stderr[-3:]))
    return lines


def _service_state(service: Mapping[str, Any]) -> str:
    details = service.get("details")
    return str(details.get("State") or "未知") if isinstance(details, Mapping) else "未知"


def _service_text(service: Mapping[str, Any]) -> str:
    if service.get("state") == "missing":
        return "未发现"
    if service.get("state") != "ok":
        return "查询失败：" + str(service.get("error", "未提供原因"))
    details = service.get("details")
    if not isinstance(details, Mapping):
        return "查询返回无效结果"
    return f"{_service_state(service)}（启动方式 {details.get('StartMode', '未知')}）"


def _adapter_lines(adapters: Mapping[str, Any]) -> list[str]:
    if adapters.get("state") != "ok":
        return ["Windows 网卡：查询失败：" + str(adapters.get("error", "未提供原因"))]
    active_hardware = adapters.get("active_hardware_count", 0)
    active_total = adapters.get("active_count", 0)
    lines = [
        f"Windows 已启用实体网卡：{active_hardware} 个"
        f"（全部已启用适配器 {active_total} 个，共 {adapters.get('adapter_count', 0)} 个）"
    ]
    items = adapters.get("adapters")
    if isinstance(items, list):
        for item in items:
            if (
                isinstance(item, Mapping)
                and item.get("hardware")
                and str(item.get("status", "")).casefold() == "up"
            ):
                lines.append(f"  - {item.get('name', '未知')}：{item.get('description', '未知')}")
    return lines


def _library_text(libraries: list[Any]) -> str:
    if not libraries:
        return "未在常见位置发现"
    values = []
    for library in libraries:
        if isinstance(library, Mapping):
            values.append(f"{library.get('location')}\\{library.get('file')}#{library.get('sha256_prefix')}")
    return "；".join(values) if values else "查询结果无效"


def _yes_no_unknown(value: object) -> str:
    if value is True:
        return "已检测到"
    if value is False:
        return "未检测到"
    return "核心未提供"


def _format_compact_value(value: object) -> str:
    if value is None:
        return "无"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _compact_rpc_data(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in ("domain_code", "retryable") if key in value}
