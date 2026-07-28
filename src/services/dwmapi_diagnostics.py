# 采集最新版 nte-mods-plugin DLL、脚本工作区和命名管道诊断。
"""Read-only diagnostics for the in-game equipment proxy DLL."""

from __future__ import annotations

import ctypes
import hashlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    GAME_EXECUTABLE_NAME,
    PLUGIN_FILENAME,
    game_executable,
    packaged_mod_workspace,
    packaged_plugin_dll,
    registered_mod_workspace,
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
    result.update({
        "ok": True,
        "game_executable": str(executable),
        "target_plugin": target_info,
        "bundled_plugin": bundled_info,
        "bundled_workspace": str(bundled_workspace),
        "registered_workspace": str(registered_workspace or ""),
        "registered_workspace_ready": bool(
            registered_workspace
            and (registered_workspace / "nte-mods.enabled").is_file()
            and (registered_workspace / "nte-mods" / "equipment.nte").is_file()
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

    lines = ["NTE Drive Calc · dwmapi 装备插件诊断"]
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
            f"打包 Mod 工作区：{result.get('bundled_workspace', '无')}",
            f"已注册 Mod 工作区：{result.get('registered_workspace') or '无'}",
            f"已注册工作区装备脚本：{'就绪' if result.get('registered_workspace_ready') else '缺失或未注册'}",
        ])
        configured_hash = str(result.get("configured_deployed_sha256") or "")
        if configured_hash:
            lines.append(
                "游戏目录 DLL 与本程序部署记录："
                + ("一致" if result.get("target_matches_recorded_deployment") else "不一致")
            )

    pipe = result.get("pipe") if isinstance(result.get("pipe"), Mapping) else {}
    lines.extend([
        "",
        "命名管道检测",
        f"管道：{pipe.get('name', EQUIPMENT_PIPE_NAME)}",
        f"状态：{pipe.get('state', '未知')}",
        f"说明：{pipe.get('message', '无')}",
    ])
    if pipe.get("error_name"):
        lines.append(f"系统结果：{pipe['error_name']}")
    lines.append("\n说明：本诊断不执行装备操作；管道“存在”仅表示游戏内插件已建立 IPC，不能代替实际装配结果。")
    return "\n".join(lines)
