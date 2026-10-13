# 在独立运行目录仅准备无界面采集 DLL，复用正式文件事务并按记录核对清理。
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from src.integrations.native_plugin_bundle import HOT_PLUGIN_LAYOUTS, NATIVE_PLUGIN_DEPLOYMENT_PATHS, inspect_native_plugin_bundle
from src.integrations.components.native_files import NativeComponentFilesDeployment, deploy_native_component_files
from src.integrations.operation_guard import require_operation
from src.integrations.components.environment import EquipmentPluginDeploymentError, game_process_running

NATIVE_LOADER_COMPONENT_ROLES = ('capture_plugin',)
NATIVE_LOADER_PAYLOAD_RELATIVE_PATH = NATIVE_PLUGIN_DEPLOYMENT_PATHS['capture_plugin']


def native_loader_payload_relative(application_root) -> str:
    bundle = inspect_native_plugin_bundle(application_root)
    return 'd3d12.dll' if bundle.layout in HOT_PLUGIN_LAYOUTS else NATIVE_LOADER_PAYLOAD_RELATIVE_PATH


def _roles(bundle) -> tuple[str, ...]:
    # 宿主布局消费完整角色集；旧采集布局只准备 capture，不借用游戏目录事实。
    return tuple(bundle.deployment_paths) if bundle.layout in HOT_PLUGIN_LAYOUTS else NATIVE_LOADER_COMPONENT_ROLES


@dataclass(frozen=True)
class NativeLoaderWorkspaceInspection:
    workspace_path: Path
    managed_files: dict[str, str]
    issues: tuple[str, ...]

    @property
    def files_compatible(self) -> bool:
        # 专用目录字节匹配不等于连接、能力或业务快照已就绪。
        return not self.issues


def inspect_native_loader_workspace(*, application_root, workspace_path) -> NativeLoaderWorkspaceInspection:
    root, directory = Path(application_root).resolve(), Path(workspace_path).resolve()
    bundle = inspect_native_plugin_bundle(root)
    if not bundle.ready:
        return NativeLoaderWorkspaceInspection(directory, {}, tuple(bundle.issues))
    expected = {bundle.deployment_paths[role]: bundle.files[bundle.roles[role]]
                for role in _roles(bundle)}
    issues = []
    for relative, digest in expected.items():
        target = directory / relative
        try:
            if target.is_symlink() or not target.resolve().is_relative_to(directory) or not target.is_file():
                issues.append(f'Loader 运行目录尚未准备组件：{relative}')
                continue
            with target.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                    issues.append(f'Loader 运行目录组件不匹配：{relative}')
        except OSError:
            issues.append(f'无法读取 Loader 运行目录组件：{relative}')
    return NativeLoaderWorkspaceInspection(directory, expected, tuple(issues))


def prepare_native_loader_workspace(*, application_root, workspace_path, operation_guard, game_running):
    require_operation(operation_guard, 'native_load')
    if (game_running or game_process_running)():
        raise EquipmentPluginDeploymentError('游戏未关闭，暂时不能更新 Loader 运行组件。请完全退出游戏后重试。')
    current = inspect_native_loader_workspace(application_root=application_root, workspace_path=workspace_path)
    if current.files_compatible:
        return NativeComponentFilesDeployment(current.workspace_path, None, current.managed_files)
    return deploy_native_component_files(
        application_root=application_root, directory_path=workspace_path,
        operation_guard=operation_guard, game_running=game_running,
        component_roles=_roles(inspect_native_plugin_bundle(application_root)),
    )
