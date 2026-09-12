# 只读核对托管组件归属，并在游戏退出后清理部署及加载登记。
"""Managed-file lifecycle facts; never infer pipe or business readiness."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
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
    game_running: bool
    observed_sha256: str = ""


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


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect_managed_plugin(
    *,
    game_executable_path: str | Path,
    deployed_sha256: str = "",
    mod_workspace_path: str | Path | None = None,
    game_running: Callable[[], bool] | None = None,
) -> ManagedPluginInspection:
    """Read file/hash/registration/process facts without opening a native pipe."""
    target = _target_path(game_executable_path)
    process_running = (game_running or game_process_running)()
    digest = ""
    if target.is_symlink() or (target.exists() and not target.is_file()):
        dll_state = "conflict"
    elif not target.exists():
        dll_state = "missing"
    else:
        try:
            digest = _digest(target)
        except OSError as exc:
            raise EquipmentPluginDeploymentError("无法核对游戏目录组件，请重新检测。") from exc
        expected = str(deployed_sha256).strip().casefold()
        dll_state = "managed" if expected and digest == expected else "conflict"
    registered, current = mod_workspace_registry_snapshot()
    if not registered:
        registry_state = "absent"
    elif mod_workspace_path and current and (
        Path(current).expanduser().resolve() == Path(mod_workspace_path).expanduser().resolve()
    ):
        registry_state = "owned"
    else:
        registry_state = "conflict"
    return ManagedPluginInspection(target, dll_state, registry_state, process_running, digest)


def cleanup_managed_plugin(
    *,
    game_executable_path: str | Path,
    deployed_sha256: str = "",
    mod_workspace_path: str | Path | None = None,
    game_running: Callable[[], bool] | None = None,
) -> ManagedPluginCleanupResult:
    """Remove managed loading entries after exit; backups are never reactivated."""
    probe = game_running or game_process_running
    facts = inspect_managed_plugin(
        game_executable_path=game_executable_path, deployed_sha256=deployed_sha256,
        mod_workspace_path=mod_workspace_path, game_running=probe,
    )
    if facts.game_running:
        return ManagedPluginCleanupResult(
            "waiting_game_exit", facts, "游戏仍在运行；请退出游戏后清理。当前 DLL 不支持安全热卸载。",
        )
    if facts.dll_state == "conflict" or facts.registry_state == "conflict":
        return ManagedPluginCleanupResult(
            "conflict", facts, "组件文件或加载配置归属未知或已修改，请手动核对；尚未清理。",
        )
    if probe():
        return ManagedPluginCleanupResult("waiting_game_exit", facts, "游戏已经启动，清理等待游戏退出。")
    if facts.dll_state == "managed":
        try:
            if facts.target_path.is_symlink() or (
                facts.target_path.exists() and _digest(facts.target_path) != facts.observed_sha256
            ):
                return ManagedPluginCleanupResult("conflict", facts, "组件在清理前发生变化，已停止清理。")
            facts.target_path.unlink(missing_ok=True)
        except OSError as exc:
            raise EquipmentPluginDeploymentError("组件清理失败，请保持游戏关闭并重新检测。") from exc
    if probe():
        return ManagedPluginCleanupResult("waiting_game_exit", facts, "游戏已经启动，剩余加载配置等待退出后清理。")
    cleanup_mod_workspace(workspace_path=mod_workspace_path)
    final = inspect_managed_plugin(
        game_executable_path=game_executable_path, deployed_sha256=deployed_sha256,
        mod_workspace_path=mod_workspace_path, game_running=probe,
    )
    if final.game_running:
        return ManagedPluginCleanupResult("waiting_game_exit", final, "文件已清理，但游戏仍需退出以结束已加载组件。")
    if final.dll_state != "missing" or final.registry_state != "absent":
        return ManagedPluginCleanupResult("conflict", final, "清理后组件或加载配置发生变化，请重新核对。")
    return ManagedPluginCleanupResult("cleaned", final, "本程序管理的游戏目录组件与加载登记已清理。")
