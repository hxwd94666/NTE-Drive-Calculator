# 执行原生组件冷部署、热更新与固定文件清理；各事务保持独立恢复边界。
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Callable

from src.integrations.native_plugin_bundle import (
    HOT_PLUGIN_LAYOUT, HOT_PLUGIN_LAYOUTS, NATIVE_PLUGIN_DEPLOYMENT_PATHS, HOT_PLUGIN_DEPLOYMENT_PATHS, PERFORMANCE_DEPLOYMENT_PATHS, HUD_DEPLOYMENT_PATHS, inspect_native_plugin_bundle,
)
from src.integrations.native_host_control import NativeHostClient, NativeHostError, NativeHostOutcomeUnknown
from src.integrations.operation_guard import require_operation
from src.integrations.legacy_game_proxy import remove_legacy_game_proxy
from src.integrations.components.environment import (
    EquipmentPluginDeploymentError,
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


class PluginDeploymentPendingCleanup(EquipmentPluginDeploymentError):
    """保留本次部分写入事实，交由退出后的清理 owner 接管。"""
    def __init__(self, message: str, *, deployment: NativePluginDeployment):
        super().__init__(message)
        self.deployment = deployment


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
    if relative not in {*NATIVE_PLUGIN_DEPLOYMENT_PATHS.values(), *HOT_PLUGIN_DEPLOYMENT_PATHS.values(), *PERFORMANCE_DEPLOYMENT_PATHS.values(), *HUD_DEPLOYMENT_PATHS.values()}:
        raise EquipmentPluginDeploymentError('组件记录包含正式布局之外的文件。')
    target = directory / relative
    if target.is_symlink() or not target.resolve().is_relative_to(directory):
        raise EquipmentPluginDeploymentError('组件目标不是可管理的游戏目录普通文件。')
    if target.exists() and not target.is_file():
        raise EquipmentPluginDeploymentError('组件目标位置不是普通文件。')
    return target


def _manual_cleanup_target(directory: Path, relative: str) -> Path:
    target = _target(directory, relative)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return target
    if (not stat.S_ISREG(info.st_mode)
            or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
        raise EquipmentPluginDeploymentError('组件目标不是可管理的游戏目录普通文件。')
    return target


def _replace_file(source: Path, target: Path, digest: str, require_idle, *, suffix: str, expected_target: str | None) -> None:
    require_idle()
    # 同目录暂存只保障单文件替换；整包失败不恢复旧 DLL。
    descriptor, temporary_name = tempfile.mkstemp(prefix='.nte-deploy-', suffix=suffix, dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    failure = None
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
    except Exception as error:
        failure = error
        if isinstance(error, OSError) and getattr(error, 'winerror', None) in {225, 226}:
            raise EquipmentPluginDeploymentError(
                f'Windows 安全防护阻止部署 {target.name}，请查看系统保护历史并核查组件来源。'
            ) from error
        if isinstance(error, FileNotFoundError):
            raise EquipmentPluginDeploymentError(
                f'部署 {target.name} 时文件消失，写入结果无法核验；请检查系统保护历史和文件占用情况。'
            ) from error
        raise
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            if failure is None:
                raise  # 暂存清理也失败时，保留更早的部署异常。

def deploy_native_component_files(
    *, application_root: str | Path, directory_path: str | Path,
    operation_guard: Callable[[str], None] | None,
    game_running: Callable[[], bool | None] | None = None,
    component_roles: tuple[str, ...] | None = None,
    expected_existing_files: Mapping[str, str | None] | None = None,
    cleanup_legacy_proxy: bool = False,
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
    paths = bundle.deployment_paths
    component_roles = tuple(paths) if component_roles is None else component_roles
    if (not component_roles or len(set(component_roles)) != len(component_roles)
            or any(role not in paths for role in component_roles)):
        raise EquipmentPluginDeploymentError('部署请求包含无效的采集组件角色。')
    # 插件与签名先部署，宿主入口最后提交，避免入口指向未完成的组件集。
    order = tuple(role for role in paths if role != 'host' and role in component_roles)
    if 'host' in component_roles:
        order += ('host',)
    sources, targets, expected = {}, {}, {}
    for role in order:
        relative = paths[role]
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
    originals: dict[str, str | None] = {}
    written: dict[str, str] = {}

    def result() -> NativeComponentFilesDeployment:
        return NativeComponentFilesDeployment(directory, None, dict(written))

    try:
        for relative, source in sources.items():
            require_idle()
            if _digest(source) != expected[relative]:
                raise EquipmentPluginDeploymentError('随附组件在部署前发生变化，已停止部署。')
            target = targets[relative]
            previous = _digest(target) if target.exists() else None
            if expected_existing_files is not None and previous != expected_existing_files[relative]:
                raise EquipmentPluginDeploymentError('组件目标在自动检测后发生变化，未覆盖现场文件。')
            originals[relative] = previous
        if cleanup_legacy_proxy and 'host' in order:
            remove_legacy_game_proxy(game_directory=directory, require_idle=require_idle)
        for relative, target in targets.items():
            require_idle()
            target = _target(directory, relative)
            previous = originals[relative]
            if (_digest(target) if target.exists() else None) != previous:
                raise EquipmentPluginDeploymentError('游戏目录组件在部署前发生变化，已停止部署。')
            target.parent.mkdir(parents=True, exist_ok=True)
            _replace_file(sources[relative], target, expected[relative], require_idle, suffix='.new', expected_target=previous)
            written[relative] = expected[relative]
            if _digest(target) != expected[relative]:
                raise EquipmentPluginDeploymentError('组件写入后校验失败。')
        require_idle()
        return result()
    except Exception as error:
        if written:
            # 普通失败只移除本次已写且字节未变的新文件；旧组件不自动恢复。
            try:
                require_idle()
                for relative in reversed(tuple(written)):
                    require_idle()
                    target = _target(directory, relative)
                    if not target.is_file() or _digest(target) != written[relative]:
                        raise EquipmentPluginDeploymentError('本次写入的组件已经变化，未覆盖现场文件。')
                    require_idle()
                    target.unlink()
                    written.pop(relative)
            except Exception as rollback_error:
                # 撤权、游戏启动或现场变化时保留实际记录，不扩大清理副作用。
                raise NativeComponentFilesPendingCleanup(
                    '组件部署未完成；已保留本次实际写入记录，等待游戏退出后清理。',
                    deployment=result(),
                ) from rollback_error
        if isinstance(error, (EquipmentPluginDeploymentError, PermissionError)):
            raise
        raise EquipmentPluginDeploymentError('原生组件部署失败，本次已写入的新组件已移除；未恢复旧组件，请重新部署。') from error


def deploy_native_plugin(
    *, application_root: str | Path, game_executable_path: str | Path,
    operation_guard: Callable[[str], None] | None,
    game_running: Callable[[], bool | None] | None = None,
    expected_existing_files: Mapping[str, str | None] | None = None,
    cleanup_legacy_proxy: bool = False,
) -> NativePluginDeployment:
    require_operation(operation_guard, 'native_load')
    executable = game_executable(game_executable_path)
    bundle = inspect_native_plugin_bundle(application_root)

    def wrap(record: NativeComponentFilesDeployment) -> NativePluginDeployment:
        return NativePluginDeployment(
            executable, record.directory / NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'],
            record.managed_files.get(NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'], ''),
            record.directory, record.backup_path, dict(record.managed_files), deployment_layout=bundle.layout,
        )

    try:
        return wrap(deploy_native_component_files(
            application_root=application_root, directory_path=executable.parent,
            operation_guard=operation_guard,
            game_running=game_running,
            expected_existing_files=expected_existing_files,
            cleanup_legacy_proxy=cleanup_legacy_proxy,
        ))
    except NativeComponentFilesPendingCleanup as error:
        raise PluginDeploymentPendingCleanup(str(error), deployment=wrap(error.deployment)) from error


def cleanup_native_component_files(
    *, directory_path: str | Path, managed_files: dict[str, str],
    game_running: Callable[[], bool | None] | None = None,
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
        # 升级后的字节可变；清理归属按正式固定文件名和路径，不依赖历史哈希。
        for relative in files:
            _target(directory, relative)
        # 先移除自动加载入口；清理不会恢复事务备份。
        ordered = sorted(files, key=lambda relative: relative != NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'])
        for relative in ordered:
            if probe():
                return NativePluginCleanupResult('waiting_game_exit', '游戏在清理过程中启动，剩余组件尚未清理。请完全退出游戏后重新检测。')
            target = _target(directory, relative)
            if target.exists():
                target.unlink()
    except EquipmentPluginDeploymentError as error:
        return NativePluginCleanupResult('conflict', str(error))
    except OSError as error:
        raise EquipmentPluginDeploymentError('无法清理已记录组件，请保持游戏关闭并重试。') from error
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '组件文件已清理，游戏仍需退出以结束已加载会话。')
    return NativePluginCleanupResult('cleaned', '已按固定文件名清理本程序记录的原生组件。')


def cleanup_native_plugin(
    *, game_executable_path: str | Path, managed_files: dict[str, str],
    game_running: Callable[[], bool | None] | None = None,
) -> NativePluginCleanupResult:
    executable = Path(str(game_executable_path).strip().strip('"')).expanduser()
    if not executable.is_absolute() or executable.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError('清理记录中的游戏主程序路径无效。')
    return cleanup_native_component_files(directory_path=executable.parent,
                                          managed_files=managed_files, game_running=game_running)


def cleanup_manual_native_plugin(
    *, application_root: str | Path, game_executable_path: str | Path,
    managed_files: dict[str, str], game_running: Callable[[], bool | None] | None = None,
) -> NativePluginCleanupResult:
    """显式清理选中目录的正式固定组件文件名，先完整预检再删除。"""
    probe = game_running or game_process_running
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '游戏未关闭，暂时不能清理组件。请完全退出游戏后重新检测。')
    executable = Path(str(game_executable_path).strip().strip('"')).expanduser()
    if not executable.is_absolute() or executable.name.casefold() != GAME_EXECUTABLE_NAME.casefold():
        raise EquipmentPluginDeploymentError('清理记录中的游戏主程序路径无效。')
    directory = executable.parent.resolve()
    recorded = dict(managed_files)
    observed: dict[str, str] = {}
    try:
        for relative in recorded:
            _manual_cleanup_target(directory, relative)
        for relative in {*NATIVE_PLUGIN_DEPLOYMENT_PATHS.values(), *HOT_PLUGIN_DEPLOYMENT_PATHS.values(), *PERFORMANCE_DEPLOYMENT_PATHS.values(), *HUD_DEPLOYMENT_PATHS.values()}:
            target = _manual_cleanup_target(directory, relative)
            if not target.exists():
                continue
            # 显式操作授权这些固定名称，包括未登记旧版；哈希仅防本次并发变化。
            observed[relative] = _digest(target)
        ordered = sorted(observed, key=lambda relative: relative != NATIVE_PLUGIN_DEPLOYMENT_PATHS['host'])
        for relative in ordered:
            if probe():
                return NativePluginCleanupResult('waiting_game_exit', '游戏在清理过程中启动，剩余组件尚未清理。请完全退出游戏后重新检测。')
            target = _manual_cleanup_target(directory, relative)
            if not target.exists():
                continue
            if _digest(target) != observed[relative]:
                return NativePluginCleanupResult('conflict', f'{relative} 在核对后发生变化，已保留剩余组件。')
            target.unlink()
    except EquipmentPluginDeploymentError as error:
        return NativePluginCleanupResult('conflict', str(error))
    except OSError as error:
        raise EquipmentPluginDeploymentError('无法清理游戏目录原生组件，请保持游戏关闭并重试。') from error
    if probe():
        return NativePluginCleanupResult('waiting_game_exit', '组件文件已清理，游戏仍需退出以结束已加载会话。')
    return NativePluginCleanupResult('cleaned', '已按固定文件名清理游戏目录原生组件。')


USER = 'NTE_PluginUser.dll'
COMBAT = 'NTE_PluginCombat.dll'


@dataclass(frozen=True)
class NativePluginUpdateResult:
    state: str
    detail: str
    managed_files: dict[str, str]
    layout: str = HOT_PLUGIN_LAYOUT


def update_native_plugins(*, application_root: Path, directory: Path, game_pid: int,
                          operation_guard, client=None) -> NativePluginUpdateResult:
    """同步更新 worker；调用方先排空原生会话，卸载与新加载分别受门禁约束。

    不终止游戏或活动采集；宿主拒绝仍在使用的管道。
    """
    def allowed():
        require_operation(operation_guard, 'native_load')

    allowed()
    root, directory = application_root.resolve(), directory.resolve()
    bundle = inspect_native_plugin_bundle(root)
    if not bundle.ready or bundle.layout not in HOT_PLUGIN_LAYOUTS:
        raise NativeHostError('当前整包不支持插件热更新。')
    paths = bundle.deployment_paths
    plugins = (USER, COMBAT) + (('NTE_PluginHUD.dll',) if 'hud_plugin' in paths else ()) + (('NTE_PluginPerformance.dll',) if 'performance_plugin' in paths else ())
    host = _target(directory, paths['host'])
    if not host.is_file() or _digest(host) != bundle.files[bundle.roles['host']]:
        return NativePluginUpdateResult('restart_required', 'D3D 宿主需要更新，请退出游戏后成套部署。', {}, bundle.layout)
    control = client or NativeHostClient(root, game_pid)
    description = control.call('describe')
    if description.get('mode') != 'calc' or description.get('pluginPolicy') != 'calc-publisher-rsa3072-sha256-v1':
        raise NativeHostError('实际驻留宿主不是本方受限插件宿主。')
    status = control.call('status')
    if not isinstance(status.get('runtimeDirectory'), str) or Path(status['runtimeDirectory']).resolve() != directory:
        raise NativeHostError('所选部署目录与实际宿主目录不同，未修改文件。')
    before = control.call('list')
    if not isinstance(before, list) or {m.get('file') for m in before} != set(plugins) or any(
        m.get('state') != 'loaded' for m in before
    ):
        raise NativeHostError('插件未处于完整稳定的已加载状态，请先恢复组件。')
    targets = {relative: _target(directory, relative) for role, relative in paths.items() if role != 'host'}
    expected = {relative: bundle.files[bundle.roles[role]] for role, relative in paths.items() if role != 'host'}
    original = {relative: _digest(path) for relative, path in targets.items()}
    # 磁盘字节与驻留映像必须相符，才接受本次冻结的回退字节。
    if any(m.get('sha256') != original['plugins/' + m['file']] for m in before):
        raise NativeHostError('磁盘插件与驻留映像不同，不能建立本次更新回退。')
    if original == expected:
        return NativePluginUpdateResult('current', '实际驻留插件已与当前整包一致。', {**original, 'd3d12.dll': _digest(host)}, bundle.layout)
    # 热更新回退是本次业务事务，不是冷部署策略；未知结果保留精确事务目录。
    staging = Path(tempfile.mkdtemp(prefix='.nte-update-', dir=directory))
    written = {}
    stopped = []
    preserve_transaction = False
    try:
        for relative, target in targets.items():
            backup = staging / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            if _digest(backup) != original[relative]:
                raise NativeHostError('旧组件在冻结期间改变，未开始更新。')
        for plugin in reversed(plugins):
            allowed()
            control.change('disable', plugin)
            stopped.append(plugin)
        for role, relative in paths.items():
            if role == 'host':
                continue
            _replace_file(root / bundle.roles[role], targets[relative], expected[relative], allowed,
                          suffix='.new', expected_target=original[relative])
            written[relative] = expected[relative]
        for plugin in plugins:
            allowed()
            control.change('enable', plugin)
            stopped.remove(plugin)
        loaded = control.call('list')
        if {m.get('file'): (m.get('state'), m.get('sha256')) for m in loaded} != {
            plugin: ('loaded', expected['plugins/' + plugin]) for plugin in plugins
        }:
            raise NativeHostOutcomeUnknown('新插件驻留映像尚未核对，不能报告更新成功。')
        return NativePluginUpdateResult('updated', '插件已更新并核对驻留映像；同步与战报需重新连接。',
                                        {**expected, 'd3d12.dll': _digest(host)}, bundle.layout)
    except NativeHostOutcomeUnknown:
        # 卸载/加载结果未知时不重发、不猜回退；上层维护租约继续保留。
        preserve_transaction = True
        raise
    except Exception as error:
        try:
            # 仅确认驻留模块已卸载才恢复旧字节；初始化失败可能仍在卸载中。
            modules = control.call('list')
            if written and any(m.get('state') != 'unloaded' for m in modules):
                raise NativeHostOutcomeUnknown('更新失败且仍有驻留映像，需要退出游戏后恢复。')
            for relative in reversed(tuple(written)):
                _replace_file(staging / relative, targets[relative], original[relative], allowed,
                              suffix='.restore', expected_target=written[relative])
            for plugin in plugins:
                if plugin in stopped:
                    allowed()
                    control.change('enable', plugin)
            written.clear()
        except Exception as recovery_error:
            preserve_transaction = True
            raise NativeHostOutcomeUnknown(f'更新未完成，回退未确认。保留事务目录：{staging}') from recovery_error
        raise NativeHostError('更新未完成；已恢复之前确认的组件状态。' + str(error)) from error
    finally:
        # 只有确认完成才清理本次创建的精确事务目录；未知时保留现场。
        if not preserve_transaction:
            if not staging.resolve().is_relative_to(directory) or not staging.name.startswith('.nte-update-'):
                raise NativeHostError('事务目录身份改变，未清理。')
            shutil.rmtree(staging)
