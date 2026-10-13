# 提供游戏路径、进程和 Npcap 探测，以及旧安装加载登记的单向清理。
"""有限发现游戏路径，并单向清理已退役工作区登记。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


GAME_EXECUTABLE_NAME = "HTGame.exe"
PLUGIN_FILENAME = "dwmapi.dll"
MOD_WORKSPACE_REGISTRY_KEY = r"Software\NTE DPS Tool\Mods Plugin"
MOD_WORKSPACE_REGISTRY_VALUE = "Workspace"
STANDARD_GAME_EXECUTABLE_RELATIVE_PATH = (
    Path("Neverness To Everness")
    / "Client"
    / "WindowsNoEditor"
    / "HT"
    / "Binaries"
    / "Win64"
    / GAME_EXECUTABLE_NAME
)
GAME_EXECUTABLE_WITHIN_INSTALL_PATH = Path(
    *STANDARD_GAME_EXECUTABLE_RELATIVE_PATH.parts[1:]
)
COMMON_GAME_LIBRARY_DIRECTORIES = (
    "games",
    "Games",
    "game",
    "Game",
    "Program Files",
    "Program Files (x86)",
)
_WINDOWS_UNINSTALL_REGISTRY_PATH = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
)


class EquipmentPluginDeploymentError(RuntimeError):
    """选择的游戏路径或组件文件未满足部署前提。"""


def game_process_running() -> bool | None:
    """返回进程观测三态：None 是未确认，不等于已退出。

    后台动作须等待确认退出；显式部署或清理沿现行 contract 交文件系统裁决。
    """

    try:
        result = subprocess.run(
            [
                "tasklist",
                "/FI",
                f"IMAGENAME eq {GAME_EXECUTABLE_NAME}",
                "/FO",
                "CSV",
                "/NH",
            ],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    expected = GAME_EXECUTABLE_NAME.casefold()
    return any(
        line.split(",", 1)[0].strip().strip('"').casefold() == expected
        for line in result.stdout.splitlines()
    )


def game_executable(path: str | Path) -> Path:
    # 资源管理器复制地址可能带引号；仅去除外层引号，不扩大路径发现范围。
    raw_path = str(path).strip().strip('"')
    candidate = Path(raw_path).expanduser().resolve()
    if not candidate.is_file() or candidate.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError(
            f"请选择游戏主程序 {GAME_EXECUTABLE_NAME}，而不是文件夹或其他可执行文件"
        )
    return candidate


def mod_workspace_registry_snapshot() -> tuple[bool, str | None]:
    """读取本应用工作区登记是否存在及其实际值，不探测游戏状态。"""

    if os.name != "nt":
        return False, None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            MOD_WORKSPACE_REGISTRY_KEY,
            access=winreg.KEY_QUERY_VALUE,
        ) as key:
            value, value_type = winreg.QueryValueEx(key, MOD_WORKSPACE_REGISTRY_VALUE)
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        raise EquipmentPluginDeploymentError("无法读取 nte-mods 工作区注册表") from exc
    if value_type != winreg.REG_SZ or not isinstance(value, str):
        return False, None
    return True, value


def cleanup_mod_workspace(*, workspace_path: str | Path | None) -> bool:
    """只删除仍归本工作区的活动登记，不恢复历史值。"""
    if os.name != "nt":
        return True
    current_exists, current_value = mod_workspace_registry_snapshot()
    if not current_exists:
        return True
    if not workspace_path:
        raise EquipmentPluginDeploymentError("加载配置归属未知，未清除其他工作区登记。")
    workspace = Path(workspace_path).expanduser().resolve()
    if not current_value or Path(current_value).expanduser().resolve() != workspace:
        raise EquipmentPluginDeploymentError("加载配置已修改，未清除其他工作区登记。")
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, MOD_WORKSPACE_REGISTRY_KEY,
            access=winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
        ) as key:
            observed, value_type = winreg.QueryValueEx(key, MOD_WORKSPACE_REGISTRY_VALUE)
            if value_type != winreg.REG_SZ or observed != current_value:
                raise EquipmentPluginDeploymentError("加载配置在清理前发生变化，已停止清理。")
            winreg.DeleteValue(key, MOD_WORKSPACE_REGISTRY_VALUE)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise EquipmentPluginDeploymentError("无法清除 nte-mods 工作区登记。") from exc
    return True


def _disk_roots() -> list[Path]:
    """只列出 Windows 卷根，不遍历目录树。"""
    roots: list[Path] = []
    if os.name == "nt":
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for offset in range(26):
            if mask & (1 << offset):
                roots.append(Path(f"{chr(ord('A') + offset)}:\\"))
    return roots


def _registry_executable_path(value: str) -> Path | None:
    """从卸载命令或图标登记值提取明确的可执行路径。"""

    raw_value = value.strip()
    if not raw_value:
        return None
    if raw_value.startswith('"'):
        closing_quote = raw_value.find('"', 1)
        if closing_quote > 1:
            return Path(raw_value[1:closing_quote])
    executable_end = raw_value.casefold().find(".exe")
    if executable_end >= 0:
        return Path(raw_value[: executable_end + len(".exe")].strip().strip('"'))
    return None


def _is_nte_registry_entry(
    key_name: str,
    *,
    display_name: str,
    display_icon: str,
) -> bool:
    normalized_name = display_name.casefold()
    icon_path = _registry_executable_path(display_icon)
    icon_name = icon_path.name.casefold() if icon_path is not None else ""
    return (
        key_name.casefold() == "yh"
        or "异环" in display_name
        or "neverness to everness" in normalized_name
        or icon_name in {"ntelauncher.exe", "ntegloballauncher.exe"}
    )


def _registry_game_roots() -> list[Path]:
    """仅读正式卸载登记中的候选安装根，后续仍须核对游戏主程序。"""

    if os.name != "nt":
        return []
    import winreg

    registry_roots: list[Path] = []
    access_views = {
        getattr(winreg, "KEY_WOW64_64KEY", 0),
        getattr(winreg, "KEY_WOW64_32KEY", 0),
    }
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for access_view in access_views:
            try:
                uninstall_key = winreg.OpenKey(
                    hive,
                    _WINDOWS_UNINSTALL_REGISTRY_PATH,
                    0,
                    winreg.KEY_READ | access_view,
                )
            except OSError:
                continue
            with uninstall_key:
                subkey_index = 0
                while True:
                    try:
                        key_name = winreg.EnumKey(uninstall_key, subkey_index)
                    except OSError:
                        break
                    subkey_index += 1
                    try:
                        product_key = winreg.OpenKey(
                            uninstall_key,
                            key_name,
                            0,
                            winreg.KEY_READ,
                        )
                    except OSError:
                        continue
                    with product_key:
                        values: dict[str, str] = {}
                        for value_name in (
                            "DisplayName",
                            "DisplayIcon",
                            "InstallLocation",
                            "UninstallString",
                        ):
                            try:
                                raw_value = winreg.QueryValueEx(
                                    product_key,
                                    value_name,
                                )[0]
                            except OSError:
                                raw_value = ""
                            values[value_name] = (
                                raw_value if isinstance(raw_value, str) else ""
                            )
                    if not _is_nte_registry_entry(
                        key_name,
                        display_name=values["DisplayName"],
                        display_icon=values["DisplayIcon"],
                    ):
                        continue
                    install_location = values["InstallLocation"].strip().strip('"')
                    if install_location:
                        registry_roots.append(Path(install_location))
                    for value_name in ("DisplayIcon", "UninstallString"):
                        registered_executable = _registry_executable_path(
                            values[value_name]
                        )
                        if registered_executable is None:
                            continue
                        launcher_root = registered_executable.parent
                        if launcher_root.name.casefold() == "ntelauncher":
                            launcher_root = launcher_root.parent
                        registry_roots.append(launcher_root)
    return registry_roots


def _candidate_game_executables(root: Path) -> tuple[Path, ...]:
    if root.name.casefold() == GAME_EXECUTABLE_NAME.casefold():
        return (root,)
    candidates = [root / STANDARD_GAME_EXECUTABLE_RELATIVE_PATH]
    if root.name.casefold() == "neverness to everness":
        candidates.insert(0, root / GAME_EXECUTABLE_WITHIN_INSTALL_PATH)
    return tuple(candidates)


def _default_game_search_roots() -> list[Path]:
    roots = list(_registry_game_roots())
    for volume_root in _disk_roots():
        roots.append(volume_root)
        roots.extend(
            volume_root / directory
            for directory in COMMON_GAME_LIBRARY_DIRECTORIES
        )
    return roots


def find_game_executables(
    search_roots: list[str | Path] | None = None,
    *,
    limit: int = 20,
) -> list[Path]:
    """按登记根与有限标准布局发现游戏，达到候选上限即停止。"""

    if limit <= 0:
        return []
    candidates: dict[str, Path] = {}
    roots = search_roots if search_roots is not None else _default_game_search_roots()
    for raw_root in roots:
        root = Path(raw_root).expanduser()
        for possible_executable in _candidate_game_executables(root):
            if not possible_executable.is_file():
                continue
            resolved = possible_executable.resolve()
            candidates.setdefault(str(resolved).casefold(), resolved)
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break
    return list(candidates.values())


def npcap_installation_present() -> bool:
    """仅判断本机 Npcap 安装文件存在，不启动程序或安装驱动。"""
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    return (Path(program_files) / "Npcap" / "NPFInstall.exe").is_file()


MSVC_RUNTIME_FILES = (
    "MSVCP140.dll",
    "MSVCP140_ATOMIC_WAIT.dll",
    "VCRUNTIME140.dll",
    "VCRUNTIME140_1.dll",
)


def probe_mod_plugin_msvc_runtime() -> dict[str, object]:
    """仅检查随附组件所需的固定四个 x64 运行库文件，不加载或安装 DLL。"""

    if os.name != "nt":
        return {"supported": False, "ready": False, "files": {}}
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    system_directory = system_root / "System32"
    files = {
        name: (system_directory / name).is_file()
        for name in MSVC_RUNTIME_FILES
    }
    return {
        "supported": True,
        "ready": all(files.values()),
        "directory": str(system_directory),
        "files": files,
    }
