# 默认只清理拥有的加载登记，手动授权时额外清理旧代理文件。
"""仅核对和清理有归属的旧登记，文件事实不代表连接或业务就绪。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from src.integrations.components.environment import (
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
    dll_state: Literal["missing", "managed", "conflict", "unmanaged"]
    registry_state: Literal["absent", "owned", "conflict"]
    registered_workspace: str | None
    game_running: bool | None


@dataclass(frozen=True)
class ManagedPluginCleanupResult:
    status: Literal["cleaned", "waiting_game_exit", "conflict"]
    inspection: ManagedPluginInspection
    detail: str


def _target_path(game_executable_path: str | Path) -> Path:
    # 游戏 EXE 已移除也沿原记录绝对目录清理，不搜索或替换归属目录。
    executable = Path(str(game_executable_path).strip().strip('"')).expanduser()
    if not executable.is_absolute() or executable.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError("清理记录中的游戏主程序路径无效。")
    parent = executable.parent.resolve()
    return parent / PLUGIN_FILENAME


def inspect_managed_plugin(
    *,
    game_executable_path: str | Path,
    mod_workspace_path: str | Path | None = None,
    game_running: Callable[[], bool | None] | None = None,
    inspect_legacy_proxy: bool = True,
) -> ManagedPluginInspection:
    """只核对该目录的正式旧文件名与登记，不以历史哈希推导归属。"""
    target = _target_path(game_executable_path)
    process_running = (game_running or game_process_running)()
    if not inspect_legacy_proxy:
        dll_state = "unmanaged"
    elif target.is_symlink() or (target.exists() and not target.is_file()):
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
    game_running: Callable[[], bool | None] | None = None,
    allow_unrecorded_workspace_adoption: bool = False,
    cleanup_legacy_proxy: bool = False,
) -> ManagedPluginCleanupResult:
    """后台只清理归属登记；显式手动退役才处理旧 dwmapi.dll。"""
    probe = game_running or game_process_running
    facts = inspect_managed_plugin(
        game_executable_path=game_executable_path,
        mod_workspace_path=mod_workspace_path, game_running=probe,
        inspect_legacy_proxy=cleanup_legacy_proxy,
    )
    # None 保留为未确认；显式清理沿既有 contract 由文件系统裁决。
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
        # 显式操作只接管本应用的确切旧登记值；下层删除前再次核对。
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
        inspect_legacy_proxy=cleanup_legacy_proxy,
    )
    if final.game_running:
        return ManagedPluginCleanupResult("waiting_game_exit", final, "加载登记已处理，游戏仍需退出以结束已加载会话。")
    if (cleanup_legacy_proxy and final.dll_state != "missing") or final.registry_state != "absent":
        return ManagedPluginCleanupResult("conflict", final, "清理后组件或加载配置发生变化，请重新核对。")
    return ManagedPluginCleanupResult("cleaned", final,
        "旧代理与本程序拥有的加载登记已清理。" if cleanup_legacy_proxy else
        "本程序拥有的旧加载登记已清理；非当前组件文件保持原样。")
