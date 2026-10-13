# 分别核对实际部署的代理文件与实际登记工作区，不把随附包当作运行事实。
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from src.integrations.native_plugin_bundle import (
    NATIVE_PLUGIN_LAYOUT, NativePluginBundleInspection, inspect_native_plugin_bundle,
)
from src.integrations.components.environment import EquipmentPluginDeploymentError, game_executable


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
    expected_paths: tuple[str, ...]
    layout: str = NATIVE_PLUGIN_LAYOUT

    @property
    def files_compatible(self) -> bool:
        return self.bundle_ready and bool(self.expected_paths) and set(self.files) == set(self.expected_paths) and all(
            item.matches_bundle for item in self.files.values()
        )


def inspect_deployed_native_plugin(
    *, application_root: str | Path, game_executable_path: str | Path,
    recorded_files: Mapping[str, str] | None = None,
    bundle_inspection: NativePluginBundleInspection | None = None,
) -> NativePluginDeploymentInspection:
    """只核对实际组件文件，不探测登记、管道或业务就绪。"""
    bundle = bundle_inspection if bundle_inspection is not None else inspect_native_plugin_bundle(application_root)
    issues = list(bundle.issues)
    files = {}
    records = recorded_files or {}
    try:
        game_directory = game_executable(game_executable_path).parent
    except EquipmentPluginDeploymentError:
        game_directory = None
        issues.append("无法核对游戏主程序位置；尚未检查原生部署文件。")
    for role, relative in bundle.deployment_paths.items():
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
        # 整包、磁盘字节、登记与前驱分别是事实；文件匹配不授予业务能力。
        matches = bundle.ready and present and bool(actual) and actual == expected and size == expected_size
        if present and not matches:
            issues.append(f"实际原生组件尚未与配套整包核对匹配：{relative}")
        recorded = records.get(relative, "")
        files[relative] = NativePluginFileInspection(
            relative, present, actual, size, expected, expected_size, bool(matches),
            bool(actual) and isinstance(recorded, str) and actual == recorded.strip().casefold(),
            bundle.ready and present and bool(actual) and actual in bundle.upgrade_from.get(relative, ()),
        )
    return NativePluginDeploymentInspection(
        game_directory, MappingProxyType(files), bundle.ready, tuple(issues),
        expected_paths=tuple(bundle.deployment_paths.values()), layout=bundle.layout,
    )
