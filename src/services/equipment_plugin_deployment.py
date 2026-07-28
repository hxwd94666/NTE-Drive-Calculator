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
MOD_PLUGIN_SIGNATURE = b"NTE_DPS_TOOL_MODS_PLUGIN_V1"
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
    workspace_registry_value_before: str | None = None
    workspace_registry_value_existed: bool = False


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


def is_mods_plugin_dll(path: str | Path) -> bool:
    """Return whether the DLL carries the public nte-mods-plugin marker."""

    candidate = plugin_dll(path)
    overlap = len(MOD_PLUGIN_SIGNATURE) - 1
    tail = b""
    try:
        with candidate.open("rb") as stream:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                if MOD_PLUGIN_SIGNATURE in tail + chunk:
                    return True
                tail = (tail + chunk)[-overlap:]
    except OSError:
        return False
    return False


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
            plugin = plugin_dll(candidate)
            if is_mods_plugin_dll(plugin):
                return plugin
    checked = "、".join(str(candidate) for candidate in candidates)
    raise EquipmentPluginDeploymentError(
        f"未找到带新版 MOD 签名的 {PLUGIN_FILENAME}；已检查：{checked}"
    )


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


def _register_mod_workspace(workspace: Path) -> tuple[bool, str | None]:
    if os.name != "nt":
        raise EquipmentPluginDeploymentError("nte-mods 工作区注册仅支持 Windows")
    import winreg

    previous_exists, previous_value = mod_workspace_registry_snapshot()
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
    return previous_exists, previous_value


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


def restore_mod_workspace(
    *,
    workspace_path: str | Path | None,
    previous_value: str | None,
    previous_value_existed: bool,
) -> bool:
    """Restore the previous registry value only while this app still owns it."""

    if workspace_path is None or os.name != "nt":
        return False
    workspace = Path(workspace_path).expanduser().resolve()
    current_exists, current_value = mod_workspace_registry_snapshot()
    if not current_exists or current_value != str(workspace):
        return False
    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            MOD_WORKSPACE_REGISTRY_KEY,
            access=winreg.KEY_SET_VALUE,
        ) as key:
            if previous_value_existed:
                winreg.SetValueEx(
                    key,
                    MOD_WORKSPACE_REGISTRY_VALUE,
                    0,
                    winreg.REG_SZ,
                    str(previous_value or ""),
                )
            else:
                try:
                    winreg.DeleteValue(key, MOD_WORKSPACE_REGISTRY_VALUE)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        raise EquipmentPluginDeploymentError("无法还原 nte-mods 工作区注册表") from exc
    return True


def prepare_mod_workspace(
    *,
    application_root: str | Path,
    writable_workspace_path: str | Path,
    register_workspace: bool = True,
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

    if register_workspace:
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
    if not is_mods_plugin_dll(source):
        raise EquipmentPluginDeploymentError(
            f"所选 {PLUGIN_FILENAME} 不是与当前脚本匹配的新版 nte-mods-plugin"
        )
    workspace = prepare_mod_workspace(
        application_root=application_root,
        writable_workspace_path=writable_workspace_path,
        register_workspace=False,
    )
    target = executable.parent / PLUGIN_FILENAME
    if source == target:
        raise EquipmentPluginDeploymentError("所选插件已经位于目标游戏目录，无需重复部署")

    source_hash = _file_sha256(source)
    target_existed_before = target.exists()
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
    try:
        registry_existed, registry_value = _register_mod_workspace(workspace)
    except EquipmentPluginDeploymentError as exc:
        try:
            if backup_path is not None and backup_path.is_file():
                shutil.copy2(backup_path, target)
            elif not target_existed_before and target.is_file():
                target.unlink()
        except OSError as rollback_exc:
            raise EquipmentPluginDeploymentError(
                "MOD 工作区注册失败，且游戏目录 DLL 自动回滚失败；"
                f"请保持游戏关闭并手动检查 {target}"
            ) from rollback_exc
        raise EquipmentPluginDeploymentError(
            "MOD 工作区注册失败，游戏目录 DLL 已回滚："
            + str(exc)
        ) from exc
    return PluginDeployment(
        executable,
        target,
        backup_path,
        source_hash,
        workspace,
        registry_value,
        registry_existed,
    )


def restore_plugin(
    *,
    game_executable_path: str | Path,
    deployed_sha256: str,
    backup_path: str | Path | None,
    mod_workspace_path: str | Path | None = None,
    workspace_registry_value_before: str | None = None,
    workspace_registry_value_existed: bool = False,
) -> bool:
    """Restore the backed-up DLL and the previous global Mod workspace."""
    executable = game_executable(game_executable_path)
    target = executable.parent / PLUGIN_FILENAME
    if not target.is_file():
        raise EquipmentPluginDeploymentError("游戏目录中没有可还原的 dwmapi.dll")
    if _file_sha256(target) != str(deployed_sha256).strip().lower():
        raise EquipmentPluginDeploymentError(
            "目标 dwmapi.dll 已被其他程序修改；为避免覆盖他人文件，已拒绝还原"
        )
    try:
        restored_backup = False
        if backup_path:
            backup = Path(backup_path).expanduser().resolve()
            if backup.is_file():
                shutil.copy2(backup, target)
                restored_backup = True
        if not restored_backup:
            target.unlink()
    except OSError as exc:
        raise EquipmentPluginDeploymentError("无法还原游戏目录中的 dwmapi.dll，请确认游戏已关闭") from exc
    return restore_mod_workspace(
        workspace_path=mod_workspace_path,
        previous_value=workspace_registry_value_before,
        previous_value_existed=workspace_registry_value_existed,
    )


def npcap_installation_present() -> bool:
    """Best-effort local Npcap detection without launching a subprocess."""
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    return (Path(program_files) / "Npcap" / "NPFInstall.exe").is_file()
