"""Read-only Windows facts that explain nte-core capture enumeration failures."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]

_POWERSHELL = "powershell.exe"
_UTF8_PREFIX = "$OutputEncoding=[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new();"
_SERVICE_QUERY = (
    _UTF8_PREFIX
    + "$ErrorActionPreference='Stop';"
    "$service=Get-CimInstance -ClassName Win32_SystemDriver -Filter \"Name='npcap'\";"
    "if($null -eq $service){'null'}else{$service|Select-Object Name,State,StartMode,Status|ConvertTo-Json -Compress}"
)
_ADAPTER_QUERY = (
    _UTF8_PREFIX
    + "$ErrorActionPreference='Stop';"
    "@(Get-NetAdapter -IncludeHidden|Select-Object Name,Status,InterfaceDescription,ifIndex,HardwareInterface)"
    "|ConvertTo-Json -Compress"
)


def collect_windows_capture_support(
    *,
    core_executable: Path,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Collect minimal non-mutating facts needed to triage Npcap enumeration.

    The report intentionally omits MAC addresses, IP addresses, remote endpoints and
    full local paths.  It observes the driver service, adapter state and DLL shadowing
    candidates; it never opens a capture handle or changes network configuration.
    """

    if os.name != "nt":
        return {"supported": False, "reason": "仅支持 Windows 抓包环境诊断"}

    runner = command_runner or _run_command
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    npcap_dir = program_files / "Npcap"
    result: dict[str, Any] = {
        "supported": True,
        "npcap_installation": {
            "directory_present": npcap_dir.is_dir(),
            "installer_tool_present": (npcap_dir / "NPFInstall.exe").is_file(),
            "diagnostic_tool_present": (npcap_dir / "DiagReport.bat").is_file(),
            "install_log_present": (npcap_dir / "install.log").is_file(),
            "driver_log_present": (npcap_dir / "NPFInstall.log").is_file(),
        },
        "driver_service": _probe_json(_SERVICE_QUERY, runner),
        "network_adapters": _probe_adapters(runner),
        "runtime_libraries": _runtime_libraries(core_executable, npcap_dir, system_root),
    }
    return result


def _run_command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=8,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _probe_json(script: str, runner: CommandRunner) -> dict[str, Any]:
    try:
        completed = runner([_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script])
    except (OSError, subprocess.SubprocessError) as exc:
        return {"state": "query_error", "error": str(exc)}
    if completed.returncode != 0:
        return {
            "state": "query_error",
            "exit_code": completed.returncode,
            "error": _compact_error(completed.stderr or completed.stdout),
        }
    output = completed.stdout.strip()
    if not output or output == "null":
        return {"state": "missing"}
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return {"state": "query_error", "error": "Windows 查询返回了不可解析结果"}
    if not isinstance(payload, (Mapping, list)):
        return {"state": "query_error", "error": "Windows 查询返回了无效对象"}
    return {"state": "ok", "details": dict(payload) if isinstance(payload, Mapping) else payload}


def _probe_adapters(runner: CommandRunner) -> dict[str, Any]:
    response = _probe_json(_ADAPTER_QUERY, runner)
    if response.get("state") != "ok":
        return response
    payload = response.get("details")
    raw_adapters = payload if isinstance(payload, list) else [payload]
    adapters = [_public_adapter(item) for item in raw_adapters if isinstance(item, Mapping)]
    active = [adapter for adapter in adapters if str(adapter.get("status", "")).casefold() == "up"]
    active_hardware = [adapter for adapter in active if adapter.get("hardware")]
    return {
        "state": "ok",
        "adapter_count": len(adapters),
        "active_count": len(active),
        "active_hardware_count": len(active_hardware),
        "adapters": adapters,
    }


def _public_adapter(adapter: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": str(adapter.get("Name") or "未知"),
        "status": str(adapter.get("Status") or "未知"),
        "description": str(adapter.get("InterfaceDescription") or "未知"),
        "hardware": bool(adapter.get("HardwareInterface")),
    }


def _runtime_libraries(core_executable: Path, npcap_dir: Path, system_root: Path) -> list[dict[str, Any]]:
    locations = (
        ("核心目录", core_executable.parent),
        ("Npcap 系统目录", system_root / "System32" / "Npcap"),
        ("System32", system_root / "System32"),
        ("Npcap 安装目录", npcap_dir),
    )
    libraries: list[dict[str, Any]] = []
    for location_name, directory in locations:
        for filename in ("wpcap.dll", "Packet.dll"):
            path = directory / filename
            if not path.is_file():
                continue
            details: dict[str, Any] = {"location": location_name, "file": filename}
            try:
                details["sha256_prefix"] = _sha256(path)[:12]
            except OSError:
                details["sha256_prefix"] = "读取失败"
            libraries.append(details)
    return libraries


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compact_error(value: str) -> str:
    return " ".join(value.split())[:240] or "Windows 查询失败"
