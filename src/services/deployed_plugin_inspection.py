# 分别核对实际部署的代理文件与实际登记工作区，不把随附包当作运行事实。
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from src.integrations.game_component_bundle import inspect_game_component_bundle
from src.integrations.legacy_game_proxy import legacy_game_proxy_present
from src.integrations.native_plugin_bundle import (
    NATIVE_PLUGIN_DEPLOYMENT_PATHS, NATIVE_PLUGIN_LAYOUT, NativePluginBundleInspection, inspect_native_plugin_bundle,
)
from src.integrations.native_capture_release import validate_native_capture_release, native_capture_capabilities
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    MOD_WORKSPACE_FILES,
    PLUGIN_FILENAME,
    game_executable,
    is_mods_plugin_dll,
    mod_workspace_registry_snapshot,
    packaged_plugin_dll,
    packaged_mod_workspace,
)


@dataclass(frozen=True)
class DeployedPluginInspection:
    proxy_present: bool
    proxy_sha256: str
    proxy_matches_record: bool
    proxy_matches_package: bool
    workspace_registered: bool
    workspace_path: Path | None
    workspace_valid: bool
    issues: tuple[str, ...]
    workspace_matches_package: bool = False
    native_capabilities: frozenset[str] = frozenset()
    equipment_script_valid: bool = False

    @property
    def compatible(self) -> bool:
        """File-level compatibility only; pipe/handshake/snapshots require separate probes."""
        return self.proxy_matches_package and self.workspace_registered and self.workspace_valid


def inspect_deployed_plugin(
    *,
    application_root: str | Path,
    game_executable_path: str | Path,
    deployed_sha256: str = "",
    mod_workspace_path: str | Path | None = None,
) -> DeployedPluginInspection:
    """Inspect installed bytes even when automatic management is disabled.

    A recorded workspace is checked against the live registry entry. With no
    deployment record, compatible manually installed files can still be detected
    by the current package hash plus the registered workspace release contract.
    """
    issues: list[str] = []
    actual = ""
    matches_package = False
    try:
        target = game_executable(game_executable_path).parent / PLUGIN_FILENAME
        present = target.is_file() and not target.is_symlink()
        if present:
            with target.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            bundle = inspect_game_component_bundle(application_root)
            if bundle.ready and bundle.layout == "legacy-mods-v1":
                matches_package = actual == bundle.files[bundle.roles["proxy"]]
            else:
                # Legacy/manual installs can be checked against packaged bytes;
                # this does not grant automatic-management readiness.
                packaged = packaged_plugin_dll(application_root)
                with packaged.open("rb") as stream:
                    expected = hashlib.file_digest(stream, "sha256").hexdigest()
                matches_package = actual == expected and is_mods_plugin_dll(target)
        else:
            issues.append("游戏目录缺少代理 DLL。")
    except (OSError, EquipmentPluginDeploymentError):
        present = bool(actual)
        issues.append("无法核对实际部署的代理 DLL 与当前配套版本。")
    if actual and not matches_package:
        issues.append("实际代理 DLL 与当前随附版本不匹配。")
    registered, live_value = mod_workspace_registry_snapshot()
    live_workspace = Path(live_value).expanduser().resolve() if registered and live_value else None
    workspace_valid = False
    workspace_matches_package = False
    native_capabilities = frozenset()
    equipment_script_valid = False
    if live_workspace is None:
        issues.append("缺少当前有效的 Mod 工作区加载登记。")
    elif mod_workspace_path and live_workspace != Path(mod_workspace_path).expanduser().resolve():
        issues.append("当前加载登记与本程序记录的工作区不一致。")
    else:
        native_capabilities = native_capture_capabilities(live_workspace)
        script = Path("nte-mods") / "equipment.nte"
        try:
            source_script = Path(application_root) / "third_party" / "mods-plugin" / "workspace" / script
            if not source_script.is_file():
                source_script = Path(application_root) / "plugins" / script
            equipment_script_valid = hashlib.sha256((live_workspace / script).read_bytes()).digest() == hashlib.sha256(source_script.read_bytes()).digest()
        except OSError:
            pass
        try:
            if not all((live_workspace / relative).is_file() for relative in MOD_WORKSPACE_FILES):
                raise ValueError("实际 Mod 工作区不完整。")
            validate_native_capture_release(live_workspace)
            workspace_valid = True
            source_workspace = packaged_mod_workspace(application_root)
            workspace_matches_package = all(
                hashlib.sha256((live_workspace / relative).read_bytes()).digest()
                == hashlib.sha256((source_workspace / relative).read_bytes()).digest()
                for relative in MOD_WORKSPACE_FILES
            )
        except (ValueError, OSError, EquipmentPluginDeploymentError) as exc:
            issues.append(str(exc))
    return DeployedPluginInspection(
        present, actual, bool(actual) and actual == deployed_sha256.strip().casefold(),
        matches_package, live_workspace is not None, live_workspace, workspace_valid, tuple(issues),
        workspace_matches_package, native_capabilities, equipment_script_valid,
    )


@dataclass(frozen=True)
class NativePluginFileInspection:
    relative_path: str
    present: bool
    sha256: str
    size_bytes: int | None
    expected_sha256: str
    expected_size_bytes: int | None
    matches_bundle: bool
    matches_record: bool
    matches_predecessor: bool = False


@dataclass(frozen=True)
class NativePluginDeploymentInspection:
    game_directory: Path | None
    files: Mapping[str, NativePluginFileInspection]
    bundle_ready: bool
    issues: tuple[str, ...]
    layout: str = NATIVE_PLUGIN_LAYOUT
    legacy_proxy_present: bool = False

    @property
    def files_compatible(self) -> bool:
        return not self.legacy_proxy_present and self.bundle_ready and set(self.files) == set(NATIVE_PLUGIN_DEPLOYMENT_PATHS.values()) and all(
            item.matches_bundle for item in self.files.values()
        )


def inspect_deployed_native_plugin(
    *, application_root: str | Path, game_executable_path: str | Path,
    recorded_files: Mapping[str, str] | None = None,
    bundle_inspection: NativePluginBundleInspection | None = None,
) -> NativePluginDeploymentInspection:
    """Inspect native files and residual legacy entry, without registry or pipe probing."""
    bundle = bundle_inspection if bundle_inspection is not None else inspect_native_plugin_bundle(application_root)
    issues = list(bundle.issues)
    files = {}
    records = recorded_files or {}
    try:
        game_directory = game_executable(game_executable_path).parent
    except EquipmentPluginDeploymentError:
        game_directory = None
        issues.append("无法核对游戏主程序位置；尚未检查原生部署文件。")
    for role, relative in NATIVE_PLUGIN_DEPLOYMENT_PATHS.items():
        source = bundle.roles.get(role, "")
        expected = bundle.files.get(source, "")
        expected_size = bundle.file_sizes.get(source)
        actual, size, present = "", None, False
        if game_directory is not None:
            target = game_directory / relative
            try:
                present = target.is_file() and not target.is_symlink()
                if present:
                    with target.open("rb") as stream:
                        actual = hashlib.file_digest(stream, "sha256").hexdigest()
                    size = target.stat().st_size
                else:
                    issues.append(f"游戏目录缺少原生组件：{relative}")
            except OSError:
                issues.append(f"无法读取实际原生组件：{relative}")
        matches = bundle.ready and present and bool(actual) and actual == expected and size == expected_size
        if present and not matches:
            issues.append(f"实际原生组件尚未与配套整包核对匹配：{relative}")
        recorded = records.get(relative, "")
        files[relative] = NativePluginFileInspection(
            relative, present, actual, size, expected, expected_size, bool(matches),
            bool(actual) and isinstance(recorded, str) and actual == recorded.strip().casefold(),
            bundle.ready and present and bool(actual) and actual in bundle.upgrade_from.get(relative, ()),
        )
    legacy_present = game_directory is not None and legacy_game_proxy_present(game_directory)
    if legacy_present:
        issues.append('游戏目录仍有旧 dwmapi.dll，原生组件管理将在游戏退出后移除。')
    return NativePluginDeploymentInspection(
        game_directory, MappingProxyType(files), bundle.ready, tuple(issues), legacy_proxy_present=legacy_present,
    )
