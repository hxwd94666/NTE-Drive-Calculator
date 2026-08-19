# 采集最新版 nte-mods-plugin DLL、脚本工作区和命名管道诊断。
"""Read-only diagnostics for the in-game equipment proxy DLL."""

from __future__ import annotations

import ctypes
import hashlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.integrations.mod_loader import (
    ModLoaderRuntimeError,
    packaged_mod_loader,
)
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    GAME_EXECUTABLE_NAME,
    MOD_SDK_CACHE_FILES,
    MOD_WORKSPACE_FILES,
    PLUGIN_FILENAME,
    game_executable,
    is_mods_plugin_dll,
    packaged_mod_workspace,
    packaged_plugin_dll,
    registered_mod_workspace,
)
from src.services.mod_plugin_loading_service import (
    MSVC_RUNTIME_FILES,
    probe_mod_plugin_msvc_runtime,
)


# Kept in sync with upstream nte_mods_ipc.h.  WaitNamedPipe only observes
# availability and never sends an equipment request or mutates game state.
EQUIPMENT_PIPE_NAME = r"\\.\pipe\nte-mods-plugin-v7"

_PIPE_ERROR_NAMES = {
    2: "ERROR_FILE_NOT_FOUND（管道不存在）",
    5: "ERROR_ACCESS_DENIED（访问被拒绝）",
    121: "ERROR_SEM_TIMEOUT（管道等待超时）",
    231: "ERROR_PIPE_BUSY（管道繁忙）",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_details(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return result
    try:
        result["size"] = path.stat().st_size
        result["sha256"] = _sha256(path)
    except OSError as exc:
        result["read_error"] = str(exc)
    return result


def probe_equipment_pipe() -> dict[str, Any]:
    """Inspect the fixed plugin pipe without claiming a connection instance.

    ``WaitNamedPipeW(..., 0)`` is non-mutating.  A busy response is useful: it
    means the pipe has been created by the game-side plugin, even though it is
    not currently free for a new connection.
    """

    result: dict[str, Any] = {"name": EQUIPMENT_PIPE_NAME, "supported": os.name == "nt"}
    if os.name != "nt":
        result.update({"state": "unsupported", "message": "仅支持 Windows 命名管道诊断"})
        return result
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wait_named_pipe = kernel32.WaitNamedPipeW
        wait_named_pipe.argtypes = (ctypes.c_wchar_p, ctypes.c_uint32)
        wait_named_pipe.restype = ctypes.c_int
        if wait_named_pipe(EQUIPMENT_PIPE_NAME, 0):
            result.update({
                "state": "available",
                "message": "已发现可连接的装备插件命名管道。",
            })
            return result
        error_code = ctypes.get_last_error()
    except OSError as exc:
        result.update({"state": "probe_error", "message": f"无法调用 Windows 命名管道诊断：{exc}"})
        return result

    error_name = _PIPE_ERROR_NAMES.get(error_code, f"Windows 错误 {error_code}")
    if error_code == 2:
        state = "missing"
        message = "未发现装备插件命名管道：DLL 未被当前游戏进程加载、插件初始化失败，或 DLL 使用了其他 IPC 版本。"
    elif error_code in {121, 231}:
        state = "busy"
        message = "已发现装备插件命名管道，但当前没有空闲连接实例；请结合极速装配运行日志判断是否为连接时机或超时问题。"
    elif error_code == 5:
        state = "access_denied"
        message = "命名管道访问被拒绝：请检查应用与游戏是否以相同权限级别运行。"
    else:
        state = "error"
        message = "命名管道探测失败；请将错误码和完整诊断一并反馈。"
    result.update({"state": state, "error_code": error_code, "error_name": error_name, "message": message})
    return result


def collect_dwmapi_diagnostics(
    *,
    game_executable_path: str | Path,
    application_root: str | Path,
    recorded_deployed_sha256: str = "",
    recorded_workspace_path: str | Path = "",
    loading_method: str = "proxy",
    loader_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect file/deployment/pipe facts required to debug fast apply.

    This intentionally does not call any ``equipment.*`` RPC and does not load,
    copy, replace, or delete any DLL.
    """

    result: dict[str, Any] = {
        "ok": False,
        "game_executable_input": str(game_executable_path or ""),
        "expected_game_executable": GAME_EXECUTABLE_NAME,
        "pipe": probe_equipment_pipe(),
        "loading_method": (
            "loader" if str(loading_method).strip().casefold() == "loader" else "proxy"
        ),
        "msvc_runtime": probe_mod_plugin_msvc_runtime(),
        "loader": dict(loader_snapshot or {}),
    }
    if not result["loader"]:
        try:
            loader = packaged_mod_loader(application_root)
            result["loader"] = {
                "phase": "stopped",
                "loader_path": str(loader),
                "loader_present": True,
            }
        except ModLoaderRuntimeError as exc:
            result["loader"] = {
                "phase": "missing_loader",
                "loader_present": False,
                "detail": str(exc),
            }
    try:
        executable = game_executable(game_executable_path)
        bundled = packaged_plugin_dll(application_root)
        bundled_workspace = packaged_mod_workspace(application_root)
    except EquipmentPluginDeploymentError as exc:
        result["error"] = str(exc)
        return result

    registered_workspace = registered_mod_workspace()
    target = executable.parent / PLUGIN_FILENAME
    bundled_info = _file_details(bundled)
    target_info = _file_details(target)
    registered_workspace_ready = bool(
        registered_workspace
        and all(
            (registered_workspace / relative).is_file()
            for relative in MOD_WORKSPACE_FILES
        )
    )
    sdk_cache: dict[str, Any] = {}
    if registered_workspace:
        sdk_cache = {
            relative.name: _file_details(registered_workspace / relative)
            for relative in MOD_SDK_CACHE_FILES
        }
    recorded_workspace = (
        Path(recorded_workspace_path).expanduser()
        if str(recorded_workspace_path or "").strip()
        else None
    )
    result.update({
        "ok": True,
        "game_executable": str(executable),
        "target_plugin": target_info,
        "bundled_plugin": bundled_info,
        "target_plugin_is_mods": bool(target.is_file() and is_mods_plugin_dll(target)),
        "bundled_plugin_is_mods": is_mods_plugin_dll(bundled),
        "bundled_workspace": str(bundled_workspace),
        "registered_workspace": str(registered_workspace or ""),
        "recorded_workspace": str(recorded_workspace or ""),
        "registered_workspace_ready": registered_workspace_ready,
        "registered_workspace_sdk_cache": sdk_cache,
        "registered_workspace_matches_record": bool(
            registered_workspace
            and recorded_workspace
            and registered_workspace.resolve() == recorded_workspace.resolve()
        ),
        "configured_deployed_sha256": str(recorded_deployed_sha256 or "").strip().lower(),
    })
    target_hash = str(target_info.get("sha256") or "")
    bundled_hash = str(bundled_info.get("sha256") or "")
    configured_hash = str(recorded_deployed_sha256 or "").strip().lower()
    result["target_matches_bundled"] = bool(target_hash and target_hash == bundled_hash)
    result["target_matches_recorded_deployment"] = bool(
        target_hash and configured_hash and target_hash == configured_hash
    )
    return result


def format_dwmapi_diagnostics(result: Mapping[str, Any]) -> str:
    """Format a copyable diagnostic report without leaking unrelated settings."""

    lines = [
        "NTE Drive Calc · Mods 插件加载诊断",
        "加载方式：" + (
            "Mod Loader（备用）"
            if result.get("loading_method") == "loader"
            else "代理 DLL（推荐）"
        ),
    ]
    if not result.get("ok"):
        lines.extend(["", "检测失败", f"原因：{result.get('error', '未选择有效的 HTGame.exe')} "])
    else:
        target = result.get("target_plugin") if isinstance(result.get("target_plugin"), Mapping) else {}
        bundled = result.get("bundled_plugin") if isinstance(result.get("bundled_plugin"), Mapping) else {}
        lines.extend([
            f"游戏主程序：{result.get('game_executable', '未知')}",
            f"游戏目录 dwmapi.dll：{'存在' if target.get('exists') else '缺失'}",
            f"游戏目录 SHA-256：{target.get('sha256', '无')}",
            f"打包 DLL SHA-256：{bundled.get('sha256', '无')}",
            f"游戏目录 DLL 与打包 DLL：{'一致' if result.get('target_matches_bundled') else '不一致或无法读取'}",
            f"游戏目录 DLL 类型：{'新版 nte-mods-plugin' if result.get('target_plugin_is_mods') else '缺失、旧版或非本插件'}",
            f"打包 Mod 工作区：{result.get('bundled_workspace', '无')}",
            f"已注册 Mod 工作区：{result.get('registered_workspace') or '无'}",
            f"已注册工作区完整性：{'就绪' if result.get('registered_workspace_ready') else '文件不完整或未注册'}",
        ])
        if result.get("recorded_workspace"):
            lines.append(
                "已注册工作区与本程序部署记录："
                + ("一致" if result.get("registered_workspace_matches_record") else "不一致")
            )
        sdk_cache = (
            result.get("registered_workspace_sdk_cache")
            if isinstance(result.get("registered_workspace_sdk_cache"), Mapping)
            else {}
        )
        sdk_binary = sdk_cache.get("NTE_SDK.bin")
        sdk_checksum = sdk_cache.get("NTE_SDK.checksum")
        sdk_binary_exists = isinstance(sdk_binary, Mapping) and bool(sdk_binary.get("exists"))
        sdk_checksum_exists = isinstance(sdk_checksum, Mapping) and bool(sdk_checksum.get("exists"))
        lines.extend([
            "运行时 SDK 缓存：" + ("已生成" if sdk_binary_exists else "尚未生成"),
            "SDK 校验记录：" + ("存在" if sdk_checksum_exists else "尚未生成"),
        ])
        configured_hash = str(result.get("configured_deployed_sha256") or "")
        if configured_hash:
            lines.append(
                "游戏目录 DLL 与本程序部署记录："
                + ("一致" if result.get("target_matches_recorded_deployment") else "不一致")
            )

    pipe = result.get("pipe") if isinstance(result.get("pipe"), Mapping) else {}
    loader = result.get("loader") if isinstance(result.get("loader"), Mapping) else {}
    runtime = (
        result.get("msvc_runtime")
        if isinstance(result.get("msvc_runtime"), Mapping)
        else {}
    )
    runtime_files = runtime.get("files") if isinstance(runtime.get("files"), Mapping) else {}
    lines.extend([
        "",
        "Mod Loader",
        f"状态：{loader.get('phase', '未知')}",
        f"文件：{loader.get('loader_path') or '未找到'}",
    ])
    if loader.get("detail"):
        lines.append(f"说明：{loader['detail']}")
    lines.extend([
        "",
        "Microsoft Visual C++ 运行库",
        f"状态：{'就绪' if runtime.get('ready') else '缺失或无法确认'}",
    ])
    for name in MSVC_RUNTIME_FILES:
        lines.append(f"{name}：{'存在' if runtime_files.get(name) else '缺失'}")
    lines.extend([
        "",
        "命名管道检测",
        f"管道：{pipe.get('name', EQUIPMENT_PIPE_NAME)}",
        f"状态：{pipe.get('state', '未知')}",
        f"说明：{pipe.get('message', '无')}",
    ])
    if pipe.get("error_name"):
        lines.append(f"系统结果：{pipe['error_name']}")
    lines.append(
        "\n说明：新版插件会为当前游戏映像自动生成 NTE_SDK.bin，并在校验记录匹配时复用；"
        "本诊断不执行装备操作。管道“存在”仅表示游戏内插件已建立 IPC，不能代替实际装配结果。"
    )
    return "\n".join(lines)
