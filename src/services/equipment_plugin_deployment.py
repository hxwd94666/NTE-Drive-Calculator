# 提供游戏路径、进程和 Npcap 探测，以及旧安装加载登记的单向清理。
"""Game discovery and one-way cleanup of retired workspace registration."""

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
    """The selected game or plugin file cannot be deployed safely."""


def game_process_running() -> bool:
    """Return whether the game executable is currently present in Windows tasks."""

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
    except (OSError, subprocess.SubprocessError) as exc:
        raise EquipmentPluginDeploymentError("无法确认游戏进程状态，请重新检测后再操作组件。") from exc
    if result.returncode != 0:
        raise EquipmentPluginDeploymentError("游戏进程检测失败，请重新检测后再操作组件。")
    expected = GAME_EXECUTABLE_NAME.casefold()
    return any(
        line.split(",", 1)[0].strip().strip('"').casefold() == expected
        for line in result.stdout.splitlines()
    )


def game_executable(path: str | Path) -> Path:
    # Explorer's “复制文件地址” commonly yields a quoted absolute path.
    raw_path = str(path).strip().strip('"')
    candidate = Path(raw_path).expanduser().resolve()
    if not candidate.is_file() or candidate.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError(
            f"请选择游戏主程序 {GAME_EXECUTABLE_NAME}，而不是文件夹或其他可执行文件"
        )
    return candidate


def mod_workspace_registry_snapshot() -> tuple[bool, str | None]:
    """Return whether the global Mods workspace value exists and its value."""

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
    """Delete only this app's active registration, without reviving older values."""
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
    """Return Windows volume roots without walking any directory tree."""
    roots: list[Path] = []
    if os.name == "nt":
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for offset in range(26):
            if mask & (1 << offset):
                roots.append(Path(f"{chr(ord('A') + offset)}:\\"))
    return roots


def _registry_executable_path(value: str) -> Path | None:
    """Extract the executable from an uninstall command or display-icon value."""

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
    """Read likely NTE install roots from Windows uninstall registration."""

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
    """Find NTE via registered install roots and bounded standard layouts."""

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
    """Best-effort local Npcap detection without launching a subprocess."""
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    return (Path(program_files) / "Npcap" / "NPFInstall.exe").is_file()
