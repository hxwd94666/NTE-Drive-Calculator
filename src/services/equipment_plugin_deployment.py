# 提供打包装备插件的显式、可恢复部署能力。
# 提供打包装备插件的显式、可恢复部署能力。
"""Explicit, reversible deployment helpers for the packaged game plugin."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil


GAME_EXECUTABLE_NAME = "HTGame.exe"
PLUGIN_FILENAME = "dwmapi.dll"
STANDARD_GAME_EXECUTABLE_RELATIVE_PATH = (
    Path("Neverness To Everness")
    / "Client"
    / "WindowsNoEditor"
    / "HT"
    / "Binaries"
    / "Win64"
    / GAME_EXECUTABLE_NAME
)


class EquipmentPluginDeploymentError(RuntimeError):
    """The selected game or plugin file cannot be deployed safely."""


@dataclass(frozen=True)
class PluginDeployment:
    game_executable: Path
    target_path: Path
    backup_path: Path | None
    deployed_sha256: str


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def game_executable(path: str | Path) -> Path:
    # Explorer's “复制文件地址” commonly yields a quoted absolute path.
    raw_path = str(path).strip().strip('"')
    candidate = Path(raw_path).expanduser().resolve()
    if not candidate.is_file() or candidate.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError(
            f"请选择游戏主程序 {GAME_EXECUTABLE_NAME}，而不是文件夹或其他可执行文件"
        )
    return candidate


def plugin_dll(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_file() or candidate.name.casefold() != PLUGIN_FILENAME:
        raise EquipmentPluginDeploymentError(
            f"请选择提供方授权的 {PLUGIN_FILENAME} 文件"
        )
    return candidate


def packaged_plugin_dll(application_root: str | Path) -> Path:
    """Return the app-root DLL shipped with this exact application build."""
    return plugin_dll(Path(application_root) / PLUGIN_FILENAME)


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


def find_game_executables(
    search_roots: list[str | Path] | None = None,
    *,
    limit: int = 20,
) -> list[Path]:
    """Check only the fixed NTE layout directly below each disk root."""
    candidates: set[Path] = set()
    roots = search_roots if search_roots is not None else _disk_roots()
    for raw_root in roots:
        root = Path(raw_root).expanduser()
        direct = root / STANDARD_GAME_EXECUTABLE_RELATIVE_PATH
        if direct.is_file():
            candidates.add(direct.resolve())
        if len(candidates) >= limit:
            break
    return sorted(candidates, key=lambda path: str(path).casefold())


def deploy_plugin(
    *,
    game_executable_path: str | Path,
    plugin_dll_path: str | Path,
    backup_directory: str | Path,
) -> PluginDeployment:
    """Copy the packaged plugin beside HTGame.exe and preserve any DLL."""
    executable = game_executable(game_executable_path)
    source = plugin_dll(plugin_dll_path)
    target = executable.parent / PLUGIN_FILENAME
    if source == target:
        raise EquipmentPluginDeploymentError("所选插件已经位于目标游戏目录，无需重复部署")

    source_hash = _file_sha256(source)
    backup_path: Path | None = None
    if target.exists() and _file_sha256(target) != source_hash:
        backup_root = Path(backup_directory).expanduser().resolve()
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_path = backup_root / f"{target.parent.name}_{PLUGIN_FILENAME}.{_file_sha256(target)[:16]}.bak"
        if not backup_path.exists():
            shutil.copy2(target, backup_path)
    try:
        shutil.copy2(source, target)
    except OSError as exc:
        raise EquipmentPluginDeploymentError(
            f"无法写入游戏目录：{target}。请关闭游戏，并以有该目录写入权限的身份重试。"
        ) from exc
    return PluginDeployment(executable, target, backup_path, source_hash)


def restore_plugin(
    *,
    game_executable_path: str | Path,
    deployed_sha256: str,
    backup_path: str | Path | None,
) -> None:
    """Restore the backed-up DLL, or remove only the exact deployed DLL."""
    executable = game_executable(game_executable_path)
    target = executable.parent / PLUGIN_FILENAME
    if not target.is_file():
        raise EquipmentPluginDeploymentError("游戏目录中没有可还原的 dwmapi.dll")
    if _file_sha256(target) != str(deployed_sha256).strip().lower():
        raise EquipmentPluginDeploymentError(
            "目标 dwmapi.dll 已被其他程序修改；为避免覆盖他人文件，已拒绝还原"
        )
    try:
        if backup_path:
            backup = Path(backup_path).expanduser().resolve()
            if backup.is_file():
                shutil.copy2(backup, target)
                return
        target.unlink()
    except OSError as exc:
        raise EquipmentPluginDeploymentError("无法还原游戏目录中的 dwmapi.dll，请确认游戏已关闭") from exc


def npcap_installation_present() -> bool:
    """Best-effort local Npcap detection without launching a subprocess."""
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    return (Path(program_files) / "Npcap" / "NPFInstall.exe").is_file()
