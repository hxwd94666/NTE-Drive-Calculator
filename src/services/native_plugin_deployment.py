# 按正式布局部署原生采集组件，并按本次写入记录回滚或清理。
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Callable

from src.integrations.native_plugin_bundle import (
    NATIVE_PLUGIN_DEPLOYMENT_PATHS, inspect_native_plugin_bundle,
)
from src.integrations.operation_guard import require_operation
from src.integrations.legacy_game_proxy import remove_legacy_game_proxy
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError, PluginDeploymentPendingCleanup,
    GAME_EXECUTABLE_NAME, game_executable, game_process_running,
)


@dataclass(frozen=True)
class NativePluginDeployment:
    game_executable: Path
    target_path: Path
    deployed_sha256: str
    workspace_path: Path
    backup_path: Path | None
    managed_files: dict[str, str]
    loading_method: str = 'native-capture'
    deployment_layout: str = 'native-capture-v1'


@dataclass(frozen=True)
class NativePluginCleanupResult:
    status: str
    detail: str


@dataclass(frozen=True)
class NativeComponentFilesDeployment:
    directory: Path
    backup_path: Path | None
    managed_files: dict[str, str]


class NativeComponentFilesPendingCleanup(EquipmentPluginDeploymentError):
    def __init__(self, message: str, *, deployment: NativeComponentFilesDeployment):
        super().__init__(message)
        self.deployment = deployment


def _digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _target(directory: Path, relative: str) -> Path:
    if relative not in NATIVE_PLUGIN_DEPLOYMENT_PATHS.values():
        raise EquipmentPluginDeploymentError('组件记录包含正式布局之外的文件。')
    target = directory / relative
    if target.is_symlink() or not target.resolve().is_relative_to(directory):
        raise EquipmentPluginDeploymentError('组件目标不是可管理的游戏目录普通文件。')
    if target.exists() and not target.is_file():
        raise EquipmentPluginDeploymentError('组件目标位置不是普通文件。')
    return target


def _replace_file(source: Path, target: Path, digest: str, require_idle, *, suffix: str, expected_target: str | None) -> None:
    require_idle()
    descriptor, temporary_name = tempfile.mkstemp(prefix='.nte-deploy-', suffix=suffix, dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        if _digest(temporary) != digest:
            raise EquipmentPluginDeploymentError('组件临时文件校验失败，未替换目标文件。')
        require_idle()
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise EquipmentPluginDeploymentError('组件目标在暂存期间改变类型，未覆盖现场文件。')
        current = _digest(target) if target.exists() else None
        if current != expected_target:
            raise EquipmentPluginDeploymentError('组件目标在暂存期间发生变化，未覆盖现场文件。')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)

def deploy_native_component_files(
    *, application_root: str | Path, directory_path: str | Path,
    backup_directory: str | Path, operation_guard: Callable[[str], None] | None,
    game_running: Callable[[], bool] | None = None,
    component_roles: tuple[str, ...] = ('capture_plugin', 'host'),
    expected_existing_files: Mapping[str, str | None] | None = None,
) -> NativeComponentFilesDeployment:
    probe = game_running or game_process_running

    def require_idle() -> None:
        require_operation(operation_guard, 'native_load')
        if probe():
            raise EquipmentPluginDeploymentError('游戏正在运行，整套组件部署需等待游戏完全退出。')

    require_idle()
    root = Path(application_root).expanduser().resolve()
    bundle = inspect_native_plugin_bundle(root)
    if not bundle.ready:
        raise EquipmentPluginDeploymentError('；'.join(bundle.issues))
    directory = Path(directory_path).expanduser().resolve()
    if (not component_roles or len(set(component_roles)) != len(component_roles)
            or any(role not in {'capture_plugin', 'host'} for role in component_roles)):
        raise EquipmentPluginDeploymentError('部署请求包含无效的采集组件角色。')
    order = tuple(role for role in ('capture_plugin', 'host') if role in component_roles)
    sources, targets, expected = {}, {}, {}
    for role in order:
        relative = NATIVE_PLUGIN_DEPLOYMENT_PATHS[role]
        source = root / bundle.roles[role]
        target = _target(directory, relative)
        if source.resolve() == target.resolve():
            raise EquipmentPluginDeploymentError('随附组件与游戏部署位置相同，无法建立部署事务。')
        sources[relative], targets[relative] = source, target
        expected[relative] = bundle.files[bundle.roles[role]]
    if expected_existing_files is not None:
        if set(expected_existing_files) != set(targets):
            raise EquipmentPluginDeploymentError('自动部署缺少完整的目标文件核对记录。')
        for relative, target in targets.items():
            previous = _digest(target) if target.exists() else None
            if previous != expected_existing_files[relative]:
                raise EquipmentPluginDeploymentError('组件目标在自动检测后发生变化，未覆盖现场文件。')
    require_idle()
    backup_root = Path(backup_directory).expanduser().resolve()
    backup_root.mkdir(parents=True, exist_ok=True)
    transaction = Path(tempfile.mkdtemp(prefix='native-capture-', dir=backup_root))
    originals: dict[str, tuple[str | None, Path | None]] = {}
    staged: dict[str, Path] = {}
    written: dict[str, str] = {}

    def result() -> NativeComponentFilesDeployment:
        return NativeComponentFilesDeployment(directory, transaction, dict(written))

    try:
        for index, (relative, source) in enumerate(sources.items()):
            require_idle()
            stage = transaction / f'{index}.new'
            shutil.copy2(source, stage)
            if _digest(stage) != expected[relative]:
                raise EquipmentPluginDeploymentError('随附组件在部署前发生变化，已停止部署。')
            staged[relative] = stage
            target = targets[relative]
            previous = _digest(target) if target.exists() else None
            if expected_existing_files is not None and previous != expected_existing_files[relative]:
                raise EquipmentPluginDeploymentError('组件目标在自动检测后发生变化，未覆盖现场文件。')
            backup = transaction / f'{index}.bak' if previous is not None else None
            if backup is not None:
                require_idle()
                shutil.copy2(target, backup)
                if _digest(backup) != previous:
                    raise EquipmentPluginDeploymentError('游戏目录组件在备份时发生变化，已停止部署。')
            originals[relative] = previous, backup
        if 'host' in order:
            remove_legacy_game_proxy(game_directory=directory, require_idle=require_idle)
        for relative, target in targets.items():
            require_idle()
            target = _target(directory, relative)
            previous, _backup = originals[relative]
            if (_digest(target) if target.exists() else None) != previous:
                raise EquipmentPluginDeploymentError('游戏目录组件在部署前发生变化，已停止部署。')
            target.parent.mkdir(parents=True, exist_ok=True)
            _replace_file(staged[relative], target, expected[relative], require_idle, suffix='.new', expected_target=previous)
            written[relative] = expected[relative]
            if _digest(target) != expected[relative]:
                raise EquipmentPluginDeploymentError('组件写入后校验失败。')
        require_idle()
        return result()
    except Exception as error:
        if written:
            try:
                require_idle()
                for relative in reversed(tuple(written)):
                    require_idle()
                    target = _target(directory, relative)
                    if not target.is_file() or _digest(target) != written[relative]:
                        raise EquipmentPluginDeploymentError('本次写入的组件已经变化，未覆盖现场文件。')
                    previous, backup = originals[relative]
                    if backup is None:
                        require_idle()
                        target.unlink()
                    else:
                        if _digest(backup) != previous:
                            raise EquipmentPluginDeploymentError('事务备份已经变化，未恢复历史文件。')
                        _replace_file(backup, target, previous, require_idle, suffix='.rollback', expected_target=written[relative])
                    written.pop(relative)
            except Exception as rollback_error:
                raise NativeComponentFilesPendingCleanup(
                    '组件部署未完成；已保留本次实际写入记录，等待游戏退出后清理。',
                    deployment=result(),
                ) from rollback_error
        if isinstance(error, (EquipmentPluginDeploymentError, PermissionError)):
            raise
        raise EquipmentPluginDeploymentError('原生组件部署失败，本次游戏目录写入已回滚。') from error


def deploy_native_plugin(
    *, application_root: str | Path, game_executable_path: str | Path,
    backup_directory: str | Path, operation_guard: Callable[[str], None] | None,
    game_running: Callable[[], bool] | None = None,
    expected_existing_files: Mapping[str, str | None] | None = None,
) -> NativePluginDeployment:
    require_operation(operation_guard, 'native_load')
    executable = game_executable(game_executable_path)

    def wrap(record: NativeComponentFilesDeployment) -> NativePluginDeployment:
        return NativePluginDeployment(
            executable, record.directory / NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'],
            record.managed_files.get(NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'], ''),
            record.directory, record.backup_path, dict(record.managed_files),
        )

    try:
        return wrap(deploy_native_component_files(
            application_root=application_root, directory_path=executable.parent,
            backup_directory=backup_directory, operation_guard=operation_guard,
            game_running=game_running,
            expected_existing_files=expected_existing_files,
        ))
    except NativeComponentFilesPendingCleanup as error:
        raise PluginDeploymentPendingCleanup(str(error), deployment=wrap(error.deployment)) from error


def cleanup_native_component_files(
    *, directory_path: str | Path, managed_files: dict[str, str],
    game_running: Callable[[], bool] | None = None,
) -> NativePluginCleanupResult:
    probe = game_running or game_process_running
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '游戏未关闭，暂时不能清理组件。请完全退出游戏后重新检测。')
    directory = Path(directory_path).expanduser()
    if not directory.is_absolute():
        raise EquipmentPluginDeploymentError('组件清理目录必须是已记录的绝对路径。')
    directory = directory.resolve()
    files = dict(managed_files)
    try:
        for relative, digest in files.items():
            target = _target(directory, relative)
            if target.exists() and _digest(target) != digest:
                return NativePluginCleanupResult('conflict', f'组件文件已变化：{relative} 与部署记录不一致。未删除文件，请核对该组件。')
        # Remove the automatic loading entry first; never restore transaction backups.
        ordered = sorted(files, key=lambda relative: relative != NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'])
        for relative in ordered:
            if probe():
                return NativePluginCleanupResult('waiting_game_exit', '游戏在清理过程中启动，剩余组件尚未清理。请完全退出游戏后重新检测。')
            target = _target(directory, relative)
            if target.exists():
                if _digest(target) != files[relative]:
                    return NativePluginCleanupResult('conflict', f'组件文件已变化：{relative} 在清理前被修改。已停止清理，请核对该组件。')
                target.unlink()
    except EquipmentPluginDeploymentError as error:
        return NativePluginCleanupResult('conflict', str(error))
    except OSError as error:
        raise EquipmentPluginDeploymentError('无法清理已记录组件，请保持游戏关闭并重试。') from error
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '组件文件已清理，游戏仍需退出以结束已加载会话。')
    return NativePluginCleanupResult('cleaned', '已清理本程序记录且哈希匹配的原生组件。')


def cleanup_native_plugin(
    *, game_executable_path: str | Path, managed_files: dict[str, str],
    game_running: Callable[[], bool] | None = None,
) -> NativePluginCleanupResult:
    executable = Path(str(game_executable_path).strip().strip('"')).expanduser()
    if not executable.is_absolute() or executable.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError('清理记录中的游戏主程序路径无效。')
    return cleanup_native_component_files(directory_path=executable.parent,
                                          managed_files=managed_files, game_running=game_running)
