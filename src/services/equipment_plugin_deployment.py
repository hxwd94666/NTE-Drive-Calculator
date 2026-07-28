# 提供打包装备插件的显式、可恢复部署能力。
# 提供打包装备插件的显式、可恢复部署能力。
"""Explicit, reversible deployment helpers for the packaged game plugin."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil


GAME_EXECUTABLE_NAME = "HTGame.exe"
PLUGIN_FILENAME = "dwmapi.dll"
PACKAGED_PLUGIN_RELATIVE_PATH = Path("third_party") / "mods-plugin" / "bin" / PLUGIN_FILENAME
LEGACY_PACKAGED_PLUGIN_RELATIVE_PATH = (
    Path("third_party") / "equipment-plugin" / "bin" / PLUGIN_FILENAME
)
PACKAGED_MOD_WORKSPACE_RELATIVE_PATH = Path("third_party") / "mods-plugin" / "workspace"
MOD_WORKSPACE_REGISTRY_KEY = r"Software\NTE DPS Tool\Mods Plugin"
MOD_WORKSPACE_REGISTRY_VALUE = "Workspace"
MOD_WORKSPACE_FILES = (
    Path("nte-mods.enabled"),
    Path("mods-plugin.version"),
    Path("README.md"),
    Path("nte-mods") / "equipment.nte",
    Path("nte-mods") / "combat-clock.nte",
)
_MANAGED_WORKSPACE_MANIFEST = ".nte-drive-calc-managed.json"
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
    workspace_path: Path


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
    """Return the packaged plugin, preferring the source-tree component layout.

    PyInstaller releases keep the DLL beside the executable for compatibility with
    existing installs, while source builds keep it under ``third_party``.
    """

    root = Path(application_root)
    candidates = (
        root / PACKAGED_PLUGIN_RELATIVE_PATH,
        root / PLUGIN_FILENAME,
        root / LEGACY_PACKAGED_PLUGIN_RELATIVE_PATH,
    )
    for candidate in candidates:
        if candidate.is_file():
            return plugin_dll(candidate)
    checked = "、".join(str(candidate) for candidate in candidates)
    raise EquipmentPluginDeploymentError(f"未找到打包的 {PLUGIN_FILENAME}；已检查：{checked}")


def packaged_mod_workspace(application_root: str | Path) -> Path:
    """Return the release-matched NTE Script workspace bundled with the plugin."""

    root = Path(application_root)
    candidates = (
        root / PACKAGED_MOD_WORKSPACE_RELATIVE_PATH,
        root / "plugins",
    )
    for candidate in candidates:
        if all((candidate / relative).is_file() for relative in MOD_WORKSPACE_FILES):
            return candidate.resolve()
    checked = "、".join(str(candidate) for candidate in candidates)
    raise EquipmentPluginDeploymentError(
        f"未找到与 {PLUGIN_FILENAME} 配套的 nte-mods 工作区；已检查：{checked}"
    )


def _managed_workspace_hashes(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EquipmentPluginDeploymentError(f"无法读取托管 Mod 工作区记录：{path}") from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(files, dict)
        or any(
            not isinstance(name, str) or not isinstance(digest, str)
            for name, digest in files.items()
        )
    ):
        raise EquipmentPluginDeploymentError(f"托管 Mod 工作区记录格式错误：{path}")
    return files


def _register_mod_workspace(workspace: Path) -> None:
    if os.name != "nt":
        raise EquipmentPluginDeploymentError("nte-mods 工作区注册仅支持 Windows")
    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            MOD_WORKSPACE_REGISTRY_KEY,
            access=winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(
                key,
                MOD_WORKSPACE_REGISTRY_VALUE,
                0,
                winreg.REG_SZ,
                str(workspace),
            )
    except OSError as exc:
        raise EquipmentPluginDeploymentError("无法注册 nte-mods 工作区") from exc


def registered_mod_workspace() -> Path | None:
    """Return the workspace currently visible to nte-mods-plugin."""

    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            MOD_WORKSPACE_REGISTRY_KEY,
            access=winreg.KEY_QUERY_VALUE,
        ) as key:
            value, value_type = winreg.QueryValueEx(key, MOD_WORKSPACE_REGISTRY_VALUE)
    except OSError:
        return None
    if value_type != winreg.REG_SZ or not isinstance(value, str) or not value.strip():
        return None
    return Path(value).expanduser()


def prepare_mod_workspace(
    *,
    application_root: str | Path,
    writable_workspace_path: str | Path,
) -> Path:
    """Install release defaults without replacing user-modified NTE Scripts."""

    source = packaged_mod_workspace(application_root)
    destination = Path(writable_workspace_path).expanduser().resolve()
    try:
        destination.mkdir(parents=True, exist_ok=True)
        manifest_path = destination / _MANAGED_WORKSPACE_MANIFEST
        previous_hashes = _managed_workspace_hashes(manifest_path)
        managed_hashes: dict[str, str] = {}

        for relative in MOD_WORKSPACE_FILES:
            source_file = source / relative
            destination_file = destination / relative
            source_hash = _file_sha256(source_file)
            relative_name = relative.as_posix()
            if destination_file.is_file():
                current_hash = _file_sha256(destination_file)
                previous_hash = previous_hashes.get(relative_name)
                if current_hash != source_hash and current_hash != previous_hash:
                    continue
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination_file)
            managed_hashes[relative_name] = source_hash

        manifest_path.write_text(
            json.dumps(
                {"version": 1, "files": managed_hashes},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except EquipmentPluginDeploymentError:
        raise
    except OSError as exc:
        raise EquipmentPluginDeploymentError(
            f"无法准备 nte-mods 工作区：{destination}"
        ) from exc

    _register_mod_workspace(destination)
    return destination


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
    application_root: str | Path,
    writable_workspace_path: str | Path,
    backup_directory: str | Path,
) -> PluginDeployment:
    """Prepare the script workspace, deploy the plugin, and preserve any DLL."""
    executable = game_executable(game_executable_path)
    source = plugin_dll(plugin_dll_path)
    workspace = prepare_mod_workspace(
        application_root=application_root,
        writable_workspace_path=writable_workspace_path,
    )
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
    return PluginDeployment(executable, target, backup_path, source_hash, workspace)


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
