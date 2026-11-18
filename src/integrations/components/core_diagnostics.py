# 收集并格式化可独立查看的 nte-core 抓包诊断报告。
"""一次性读取 Core 与 Windows 诊断事实，不拥有持续诊断任务。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from src.domain.localization.catalog import TextCatalog, localized_text
from src.integrations.native_core.nte_core import (
    NteCoreClient,
    NteCoreError,
    NteCoreRpcError,
    resolve_nte_core_executable,
)
from src.integrations.vision.windows_capture_diagnostics import collect_windows_capture_support


DiagnosticResult = dict[str, Any]
_LOCALE_PREFIX = "integration.core_diagnostics."


def _text(
    text_catalog: TextCatalog | None,
    suffix: str,
    fallback: str,
    **values: object,
) -> str:
    """本文件用户可见文案统一挂在本模块前缀下；目录缺该 key 时逐字回退中文基线。"""

    return localized_text(text_catalog, f"{_LOCALE_PREFIX}{suffix}", fallback, **values)


def collect_nte_core_diagnostics(
    *,
    cwd: str | Path | None = None,
    client_factory: Callable[[Path], NteCoreClient] | None = None,
) -> DiagnosticResult:
    """只发 capture.detect 这一种业务 RPC，不启动采集或改变网络配置。

    Windows 支持事实只读，不在结果中加入地址、MAC 或 endpoint。
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
            raise TypeError("capture.detect 返回了无效结果")  # ui-text: internal
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
        # 成功和异常路径均收尾本次 client；不建立第二会话或诊断重试。
        if client is not None:
            client.close()
    return result


def format_nte_core_diagnostics(
    result: Mapping[str, Any], *, text_catalog: TextCatalog | None = None,
) -> str:
    """报告只呈现已观测的排障事实，缺失不伪装成零或完整就绪。"""

    lines = [_text(text_catalog, "report.title", "NTE Drive Calc · nte-core 抓包诊断")]
    core = result.get("core")
    if isinstance(core, Mapping):
        unknown = _text(text_catalog, "value.unknown", "未知")
        lines.append(
            _text(
                text_catalog,
                "report.core",
                "核心：v{version}（协议 {protocol}）",
                version=core.get("version", unknown),
                protocol=core.get("protocol_version", unknown),
            )
        )

    detected = result.get("capture_detect")
    if not isinstance(detected, Mapping):
        lines.extend(_format_core_error(result, text_catalog=text_catalog))
        return "\n".join(lines)

    support = result.get("windows_capture_support")
    lines.extend(_format_capture_summary(detected, text_catalog=text_catalog))
    if isinstance(support, Mapping):
        lines.extend(_format_windows_support(support, text_catalog=text_catalog))
    lines.extend(_format_next_step(
        detected, support if isinstance(support, Mapping) else {}, text_catalog=text_catalog,
    ))
    return "\n".join(lines)


def capture_device_names(detected: Mapping[str, Any]) -> list[str]:
    """按正式设备名去重，仅供显式选择，不把显示名推成能力。"""

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


def _format_capture_summary(
    detected: Mapping[str, Any], *, text_catalog: TextCatalog | None = None,
) -> list[str]:
    devices = capture_device_names(detected)
    lines = [
        "",
        _text(text_catalog, "summary.heading", "抓包枚举"),
        _text(
            text_catalog,
            "summary.game_process",
            "游戏进程：{state}",
            state=_yes_no_unknown(detected.get("game_process_detected"), text_catalog=text_catalog),
        ),
        _text(
            text_catalog,
            "summary.game_network",
            "游戏网络连接：{state}",
            state=_yes_no_unknown(detected.get("local_ip_detected"), text_catalog=text_catalog),
        ),
        _text(text_catalog, "summary.npcap_devices", "Npcap 枚举设备：{count} 个", count=len(devices)),
        _text(
            text_catalog,
            "summary.auto_select",
            "自动选择：{device}",
            device=_format_compact_value(detected.get("recommended_device"), text_catalog=text_catalog),
        ),
    ]
    if devices:
        lines.append(_text(text_catalog, "summary.devices_present", "已发现可选网卡；默认由 nte-core 自动选择。"))
    else:
        lines.extend(
            [
                _text(
                    text_catalog,
                    "summary.core_enum_error",
                    "核心枚举错误详情：协议未提供（核心仅返回零设备）。",
                ),
                _text(text_catalog, "summary.per_adapter_reason", "逐网卡过滤原因：协议未提供。"),
            ]
        )
    return lines


def _format_windows_support(
    support: Mapping[str, Any], *, text_catalog: TextCatalog | None = None,
) -> list[str]:
    heading = _text(text_catalog, "windows.heading", "Windows 抓包环境")
    if support.get("supported") is False:
        reason = support.get(
            "reason",
            _text(text_catalog, "windows.unsupported_reason", "当前系统未提供 Windows 探测"),
        )
        return ["", heading, str(reason)]
    installation = support.get("npcap_installation")
    service = support.get("driver_service")
    adapters = support.get("network_adapters")
    libraries = support.get("runtime_libraries")
    lines = ["", heading]
    if isinstance(installation, Mapping):
        installed = bool(installation.get("directory_present"))
        tool_ready = bool(installation.get("installer_tool_present"))
        lines.append(
            _text(text_catalog, "windows.npcap_ready", "Npcap 安装：已检测到")
            if installed and tool_ready
            else _text(text_catalog, "windows.npcap_incomplete", "Npcap 安装：目录或驱动工具不完整")
        )
        if installation.get("driver_log_present"):
            lines.append(_text(text_catalog, "windows.driver_log", "Npcap 驱动日志：可用（NPFInstall.log）"))
    if isinstance(service, Mapping):
        lines.append(_text(
            text_catalog,
            "windows.driver_service",
            "Npcap 驱动服务：{state}",
            state=_service_text(service, text_catalog=text_catalog),
        ))
    if isinstance(adapters, Mapping):
        lines.extend(_adapter_lines(adapters, text_catalog=text_catalog))
    if isinstance(libraries, list):
        lines.append(_text(
            text_catalog,
            "windows.libraries",
            "抓包 DLL 候选：{libraries}",
            libraries=_library_text(libraries, text_catalog=text_catalog),
        ))
    return lines


def _format_next_step(
    detected: Mapping[str, Any],
    support: Mapping[str, Any],
    *,
    text_catalog: TextCatalog | None = None,
) -> list[str]:
    devices = capture_device_names(detected)
    heading = _text(text_catalog, "next_step.heading", "下一步")
    if devices:
        if detected.get("recommended_device") is None:
            return [
                "",
                heading,
                _text(
                    text_catalog,
                    "next_step.no_recommendation",
                    "未获得自动推荐；先保持自动选择并重新启动背包同步。持续失败时再使用高级排障。",
                ),
            ]
        return [
            "",
            heading,
            _text(
                text_catalog,
                "next_step.enumeration_ready",
                "抓包环境已满足枚举条件；若同步仍失败，请提交同步阶段错误。",
            ),
        ]

    installation = support.get("npcap_installation")
    service = support.get("driver_service")
    adapters = support.get("network_adapters")
    if isinstance(installation, Mapping) and not installation.get("installer_tool_present"):
        action = _text(
            text_catalog,
            "next_step.action_install_incomplete",
            "Npcap 安装不完整：以管理员身份修复或重装 Npcap，完成后重启 Windows。",
        )
    elif isinstance(service, Mapping) and service.get("state") == "missing":
        action = _text(
            text_catalog,
            "next_step.action_service_missing",
            "未发现 Npcap 驱动服务：修复或重装 Npcap，完成后重启 Windows。",
        )
    elif isinstance(service, Mapping) and _service_state(
        service, text_catalog=text_catalog,
    ).casefold() != "running":
        action = _text(
            text_catalog,
            "next_step.action_service_stopped",
            "Npcap 驱动未运行：先检查服务错误与 NPFInstall.log，再修复或重装 Npcap。",
        )
    elif isinstance(adapters, Mapping) and adapters.get("state") == "ok" and not adapters.get("active_count"):
        action = _text(
            text_catalog,
            "next_step.action_no_adapter",
            "Windows 未检测到已启用的网络适配器：连接并启用实际联网的 Wi-Fi 或以太网后重试。",
        )
    else:
        action = _text(
            text_catalog,
            "next_step.action_no_device",
            "Windows 已有 Npcap/网卡线索，但核心枚举为 0：检查 VPN、加速器、虚拟网卡和终端防护的网络过滤驱动；"
            "提交本报告及 NPFInstall.log。",
        )
    return ["", heading, action]


def _format_core_error(
    result: Mapping[str, Any], *, text_catalog: TextCatalog | None = None,
) -> list[str]:
    unknown = _text(text_catalog, "value.unknown", "未知")
    lines = [
        "",
        _text(text_catalog, "core_failure.heading", "核心调用失败"),
        _text(
            text_catalog,
            "core_failure.category",
            "错误类别：{name}",
            name=result.get("error_type", unknown),
        ),
        _text(
            text_catalog,
            "core_failure.code",
            "错误码：{code}",
            code=result.get("domain_code")
            or result.get("rpc_code")
            or _text(text_catalog, "value.unspecified", "未提供"),
        ),
        _text(
            text_catalog,
            "core_failure.message",
            "信息：{message}",
            message=result.get("error", unknown),
        ),
    ]
    stderr = result.get("stderr")
    if isinstance(stderr, list) and stderr:
        lines.append(_text(
            text_catalog,
            "core_failure.stderr",
            "核心输出：{output}",
            output=" | ".join(str(item) for item in stderr[-3:]),
        ))
    return lines


def _service_state(service: Mapping[str, Any], *, text_catalog: TextCatalog | None = None) -> str:
    details = service.get("details")
    unknown = _text(text_catalog, "value.unknown", "未知")
    return str(details.get("State") or unknown) if isinstance(details, Mapping) else unknown


def _service_text(service: Mapping[str, Any], *, text_catalog: TextCatalog | None = None) -> str:
    if service.get("state") == "missing":
        return _text(text_catalog, "service.missing", "未发现")
    if service.get("state") != "ok":
        return _text(
            text_catalog,
            "service.query_failed",
            "查询失败：{error}",
            error=str(service.get(
                "error",
                _text(text_catalog, "value.reason_unspecified", "未提供原因"),
            )),
        )
    details = service.get("details")
    if not isinstance(details, Mapping):
        return _text(text_catalog, "service.invalid_result", "查询返回无效结果")
    return _text(
        text_catalog,
        "service.detail",
        "{state}（启动方式 {start_mode}）",
        state=_service_state(service, text_catalog=text_catalog),
        start_mode=details.get("StartMode", _text(text_catalog, "value.unknown", "未知")),
    )


def _adapter_lines(
    adapters: Mapping[str, Any], *, text_catalog: TextCatalog | None = None,
) -> list[str]:
    if adapters.get("state") != "ok":
        return [_text(
            text_catalog,
            "adapters.query_failed",
            "Windows 网卡：查询失败：{error}",
            error=str(adapters.get(
                "error",
                _text(text_catalog, "value.reason_unspecified", "未提供原因"),
            )),
        )]
    active_hardware = adapters.get("active_hardware_count", 0)
    active_total = adapters.get("active_count", 0)
    lines = [
        _text(
            text_catalog,
            "adapters.summary",
            "Windows 已启用实体网卡：{hardware} 个（全部已启用适配器 {active} 个，共 {total} 个）",
            hardware=active_hardware,
            active=active_total,
            total=adapters.get("adapter_count", 0),
        )
    ]
    items = adapters.get("adapters")
    if isinstance(items, list):
        for item in items:
            if (
                isinstance(item, Mapping)
                and item.get("hardware")
                and str(item.get("status", "")).casefold() == "up"
            ):
                lines.append("  - " + _text(
                    text_catalog,
                    "adapters.item",
                    "{name}：{description}",
                    name=item.get("name", _text(text_catalog, "value.unknown", "未知")),
                    description=item.get("description", _text(text_catalog, "value.unknown", "未知")),
                ))
    return lines


def _library_text(libraries: list[Any], *, text_catalog: TextCatalog | None = None) -> str:
    if not libraries:
        return _text(text_catalog, "dll.none_found", "未在常见位置发现")
    values = []
    for library in libraries:
        if isinstance(library, Mapping):
            values.append(f"{library.get('location')}\\{library.get('file')}#{library.get('sha256_prefix')}")
    return "；".join(values) if values else _text(text_catalog, "dll.invalid", "查询结果无效")


def _yes_no_unknown(value: object, *, text_catalog: TextCatalog | None = None) -> str:
    if value is True:
        return _text(text_catalog, "yes_no.detected", "已检测到")
    if value is False:
        return _text(text_catalog, "yes_no.not_detected", "未检测到")
    return _text(text_catalog, "yes_no.not_provided", "核心未提供")


def _format_compact_value(value: object, *, text_catalog: TextCatalog | None = None) -> str:
    if value is None:
        return _text(text_catalog, "value.none", "无")
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _compact_rpc_data(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in ("domain_code", "retryable") if key in value}
