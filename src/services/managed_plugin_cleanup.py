# 按游戏目录旧代理文件名清理，并核对加载登记和游戏退出状态。
"""Managed-file lifecycle facts; never infer pipe or business readiness."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    GAME_EXECUTABLE_NAME,
    PLUGIN_FILENAME,
    cleanup_mod_workspace,
    game_process_running,
    mod_workspace_registry_snapshot,
)


@dataclass(frozen=True)
class ManagedPluginInspection:
    target_path: Path
    dll_state: Literal["missing", "managed", "conflict"]
    registry_state: Literal["absent", "owned", "conflict"]
    registered_workspace: str | None
    game_running: bool


@dataclass(frozen=True)
class ManagedPluginCleanupResult:
    status: Literal["cleaned", "waiting_game_exit", "conflict"]
    inspection: ManagedPluginInspection
    detail: str


def _target_path(game_executable_path: str | Path) -> Path:
    # A removed game executable must not prevent cleaning its owned proxy.
    executable = Path(str(game_executable_path).strip().strip('"')).expanduser()
    if not executable.is_absolute() or executable.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError("清理记录中的游戏主程序路径无效。")
    parent = executable.parent.resolve()
    return parent / PLUGIN_FILENAME


def inspect_managed_plugin(
    *,
    game_executable_path: str | Path,
    mod_workspace_path: str | Path | None = None,
    game_running: Callable[[], bool] | None = None,
) -> ManagedPluginInspection:
    """Inspect the game-local legacy filename and registration without hashing."""
    target = _target_path(game_executable_path)
    process_running = (game_running or game_process_running)()
    if target.is_symlink() or (target.exists() and not target.is_file()):
        dll_state = "conflict"
    elif not target.exists():
        dll_state = "missing"
    else:
        dll_state = "managed"
    registered, current = mod_workspace_registry_snapshot()
    if not registered:
        registry_state = "absent"
    elif mod_workspace_path and current and (
        Path(current).expanduser().resolve() == Path(mod_workspace_path).expanduser().resolve()
    ):
        registry_state = "owned"
    else:
        registry_state = "conflict"
    return ManagedPluginInspection(target, dll_state, registry_state, current, process_running)


def cleanup_managed_plugin(
    *,
    game_executable_path: str | Path,
    mod_workspace_path: str | Path | None = None,
    game_running: Callable[[], bool] | None = None,
    allow_unrecorded_workspace_adoption: bool = False,
) -> ManagedPluginCleanupResult:
    """Remove the game-local dwmapi.dll regardless of its version or recorded hash."""
    probe = game_running or game_process_running
    facts = inspect_managed_plugin(
        game_executable_path=game_executable_path,
        mod_workspace_path=mod_workspace_path, game_running=probe,
    )
    if facts.game_running:
        return ManagedPluginCleanupResult(
            "waiting_game_exit", facts, "游戏未关闭，暂时不能清理组件。请完全退出游戏后重新检测。",
        )
    if facts.dll_state == "conflict":
        return ManagedPluginCleanupResult(
            "conflict", facts, "组件路径不是普通文件：dwmapi.dll。请检查游戏目录中的同名目录或链接。",
        )
    cleanup_workspace = mod_workspace_path
    if facts.registry_state == "conflict":
        if (
            not allow_unrecorded_workspace_adoption
            or bool(mod_workspace_path)
            or not facts.registered_workspace
        ):
            return ManagedPluginCleanupResult(
                "conflict", facts, "加载配置与部署记录不一致，未清理。请核对当前注册的 Mod 工作区。",
            )
        # The explicit cleanup/deploy action adopts only this application's exact
        # legacy registry value. cleanup_mod_workspace rechecks it before deletion.
        cleanup_workspace = facts.registered_workspace
    if probe():
        return ManagedPluginCleanupResult("waiting_game_exit", facts, "游戏在清理前启动。请完全退出游戏后重新检测。")
    if facts.dll_state == "managed":
        try:
            if facts.target_path.is_symlink() or (
                facts.target_path.exists() and not facts.target_path.is_file()
            ):
                return ManagedPluginCleanupResult("conflict", facts, "组件文件已变化：dwmapi.dll 在清理前被修改。已停止清理，请核对该组件。")
            facts.target_path.unlink(missing_ok=True)
        except OSError as exc:
            raise EquipmentPluginDeploymentError("组件清理失败，请保持游戏关闭并重新检测。") from exc
    if probe():
        return ManagedPluginCleanupResult("waiting_game_exit", facts, "游戏在清理过程中启动，加载配置尚未清理。请退出游戏后重新检测。")
    cleanup_mod_workspace(workspace_path=cleanup_workspace)
    final = inspect_managed_plugin(
        game_executable_path=game_executable_path,
        mod_workspace_path=mod_workspace_path, game_running=probe,
    )
    if final.game_running:
        return ManagedPluginCleanupResult("waiting_game_exit", final, "文件已清理，但游戏仍需退出以结束已加载组件。")
    if final.dll_state != "missing" or final.registry_state != "absent":
        return ManagedPluginCleanupResult("conflict", final, "清理后组件或加载配置发生变化，请重新核对。")
    return ManagedPluginCleanupResult("cleaned", final, "本程序管理的游戏目录组件与加载登记已清理。")
