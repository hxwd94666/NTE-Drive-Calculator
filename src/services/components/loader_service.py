# 编排代理 DLL 与备用 Mod Loader 的互斥加载方式。
"""编排加载方式、授权和记录；ModLoaderRuntime 仍拥有进程生命周期。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from src.integrations.mod_loader import (
    MOD_LOADER_STOP_TIMEOUT_MS,
    ModLoaderRuntime,
    ModLoaderRuntimeError,
    ModLoaderRuntimeSnapshot,
    game_launcher_candidates,
    game_launcher_executable,
)
from src.integrations.launcher_process import LauncherProcessProbeError, selected_launcher_running
from src.integrations.operation_guard import require_operation
from src.integrations.legacy_game_proxy import remove_legacy_game_proxy
from src.integrations.game_component_bundle import inspect_game_component_bundle
from src.integrations.native_plugin_bundle import NATIVE_PLUGIN_LAYOUTS, HOT_PLUGIN_LAYOUTS, HOT_PLUGIN_DEPLOYMENT_PATHS, PERFORMANCE_DEPLOYMENT_PATHS, HUD_DEPLOYMENT_PATHS
from src.integrations.components.loader_workspace import (
    native_loader_payload_relative, inspect_native_loader_workspace, prepare_native_loader_workspace,
)
from src.integrations.components.native_files import (
    NativeComponentFilesDeployment, NativeComponentFilesPendingCleanup,
    NativePluginCleanupResult, cleanup_native_component_files,
)
from src.integrations.components.environment import (
    EquipmentPluginDeploymentError, game_executable, game_process_running,
    MSVC_RUNTIME_FILES, probe_mod_plugin_msvc_runtime,
)


class ModPluginLoadingError(RuntimeError):
    """The requested loading-method transition is unsafe or unavailable."""


class ModPluginLoadingWaiting(ModPluginLoadingError):
    """A running or unverified launcher requires a later retry."""


class ModPluginLoadingPendingCleanup(ModPluginLoadingError):
    def __init__(self, message: str, *, native_workspace: NativeComponentFilesDeployment):
        super().__init__(message)
        self.native_workspace = native_workspace


@dataclass(frozen=True)
class ModPluginLoaderStartResult:
    runtime: ModLoaderRuntimeSnapshot
    workspace_path: Path
    native_workspace: NativeComponentFilesDeployment | None = None


class ModLoaderRuntimeContract(Protocol):
    def snapshot(
        self,
        *,
        payload_path: str | Path,
    ) -> ModLoaderRuntimeSnapshot: ...

    def start(
        self,
        *,
        payload_path: str | Path,
        launcher_path: str | Path,
        payload_load_mode: str = "loadlibrary",
        launch_guard: Callable[[], None] | None = None,
    ) -> ModLoaderRuntimeSnapshot: ...

    def stop(self, *, timeout_ms: int = MOD_LOADER_STOP_TIMEOUT_MS) -> bool: ...

    def close(self) -> None: ...

    def require_payload_load_mode(self, mode: str) -> None: ...

    def require_payload_kind(self, kind: str) -> None: ...


class ModPluginLoadingService:
    """保证游戏目录部署与专用 Loader 互斥，不将文件或进程状态当业务成功。"""

    def __init__(
        self,
        *,
        application_root: str | Path,
        runtime: ModLoaderRuntimeContract | None = None,
        operation_guard: Callable[[str], None] | None = None,
        game_running: Callable[[], bool | None] | None = None,
        native_workspace_path: str | Path | None = None,
    ) -> None:
        self._application_root = Path(application_root).resolve()
        self._runtime = runtime or ModLoaderRuntime(
            application_root=self._application_root
        )
        self._operation_guard = operation_guard
        self._game_running = game_running or game_process_running
        self._active_payload_sha256 = ""
        self._native_workspace_root = Path(native_workspace_path or self._application_root / 'config/native-loader').expanduser().resolve()
        self._native_workspace: NativeComponentFilesDeployment | None = None

    @property
    def active_payload_sha256(self) -> str:
        """返回本次启动冻结的哈希，不随当前文件变化重新解释。"""
        return self._active_payload_sha256


    def _require_load_allowed(self, scoped_guard: Callable[[str], None] | None = None) -> None:
        require_operation(self._operation_guard, "native_load")
        if scoped_guard is not None:
            scoped_guard("native_load")
        if self._game_running():
            raise ModPluginLoadingError("游戏正在运行，组件加载与更新需等待游戏退出。")

    @property
    def native_workspace_path(self) -> Path:
        return self._native_workspace_root

    @property
    def pending_native_workspace_path(self) -> Path | None:
        return self._native_workspace.directory if self._native_workspace is not None else None

    @property
    def native_workspace_files_compatible(self) -> bool:
        return self.inspect_native_workspace().files_compatible

    @property
    def native_workspace_record(self) -> NativeComponentFilesDeployment | None:
        return self._native_workspace

    def inspect_native_workspace(self, path: str | Path | None = None):
        return inspect_native_loader_workspace(application_root=self._application_root,
                                               workspace_path=path or self._native_workspace_root)

    def restore_native_workspace_record(self, *, workspace_path, managed_files, backup_path=None) -> None:
        directory = Path(workspace_path).expanduser().resolve()
        if directory != self._native_workspace_root:
            raise ModPluginLoadingError('Loader 记录与本机专用运行目录不一致。')
        allowed_paths = {'NTE_Capture.dll'}
        if inspect_game_component_bundle(self._application_root).layout in HOT_PLUGIN_LAYOUTS:
            allowed_paths.update(HOT_PLUGIN_DEPLOYMENT_PATHS.values())
            allowed_paths.update(PERFORMANCE_DEPLOYMENT_PATHS.values())
            allowed_paths.update(HUD_DEPLOYMENT_PATHS.values())
        if (not isinstance(managed_files, dict) or not set(managed_files).issubset(allowed_paths)
                or any(not isinstance(value, str) or len(value) != 64 or any(char not in '0123456789abcdef' for char in value)
                       for value in managed_files.values())):
            raise ModPluginLoadingError('Loader 运行目录记录包含无效文件或摘要。')
        self._native_workspace = NativeComponentFilesDeployment(
            directory, Path(backup_path).resolve() if backup_path else None,
            dict(managed_files),
        )

    def require_native_loader_supported(self) -> None:
        """先核对能力再移除归属旧入口；此预检不暂存文件或启动进程。"""
        self._require_load_allowed()
        bundle = inspect_game_component_bundle(self._application_root)
        if bundle.layout not in NATIVE_PLUGIN_LAYOUTS or not bundle.ready:
            raise ModPluginLoadingError('原生整包不可用：' + '；'.join(bundle.issues))
        self._require_msvc_runtime()
        try:
            self._runtime.require_payload_load_mode('loadlibrary')
            if bundle.layout in HOT_PLUGIN_LAYOUTS:
                self._runtime.require_payload_kind('nte_calc_host_v1')
        except ModLoaderRuntimeError as error:
            raise ModPluginLoadingError(str(error)) from error

    def _retain_native_workspace(self, record: NativeComponentFilesDeployment) -> None:
        previous = self._native_workspace
        managed = {}
        if previous is not None and previous.directory == record.directory:
            # 跨升级保留正式归属文件名；清理不以旧版本哈希阻塞已升级文件。
            managed.update(previous.managed_files)
        managed.update(record.managed_files)
        self._native_workspace = NativeComponentFilesDeployment(record.directory, record.backup_path, managed)

    def cleanup_native_workspace(self) -> NativePluginCleanupResult:
        record = self._native_workspace
        if record is None:
            return NativePluginCleanupResult('cleaned', '没有待清理的 Loader 运行目录记录。')
        result = cleanup_native_component_files(directory_path=record.directory,
                                                managed_files=record.managed_files, game_running=self._game_running)
        if result.status == 'cleaned':
            self._native_workspace = None
            self._active_payload_sha256 = ''
        return result

    def snapshot(self) -> ModLoaderRuntimeSnapshot:
        try:
            bundle = inspect_game_component_bundle(self._application_root)
            if not bundle.ready:
                raise ModPluginLoadingError('；'.join(bundle.issues))
            payload = self._native_workspace_root / native_loader_payload_relative(self._application_root)
            return self._runtime.snapshot(payload_path=payload)
        except (EquipmentPluginDeploymentError, ModLoaderRuntimeError) as exc:
            raise ModPluginLoadingError(str(exc)) from exc



    def start_loader(self, *, game_executable_path: str | Path, writable_workspace_path: str | Path,
                     scoped_guard: Callable[[str], None] | None = None,
                     cleanup_legacy_proxy: bool = False) -> ModPluginLoaderStartResult:
        if Path(writable_workspace_path).expanduser().resolve() != self._native_workspace_root:
            raise ModPluginLoadingError('原生 Loader 必须使用本机配置的专用运行目录。')
        self.require_native_loader_supported()
        self._require_load_allowed(scoped_guard)
        executable = game_executable(game_executable_path)
        launcher = game_launcher_executable(executable)
        try:
            if any(selected_launcher_running(candidate) for candidate in game_launcher_candidates(executable)):
                raise ModPluginLoadingWaiting("官方启动器仍在运行；请关闭启动器和游戏后再启动 Loader。")
        except (LauncherProcessProbeError, OSError) as error:
            raise ModPluginLoadingWaiting(str(error)) from error
        payload = self._native_workspace_root / native_loader_payload_relative(self._application_root)
        current = self._runtime.snapshot(payload_path=payload)
        if current.phase not in {'running', 'stopped', 'missing_payload'}:
            raise ModPluginLoadingError(current.detail or 'Loader 当前状态不可启动。')

        def guard(capability):
            require_operation(self._operation_guard, capability)
            if scoped_guard is not None:
                scoped_guard(capability)
            self._require_load_allowed(scoped_guard)
        def require_idle():
            guard('native_load')
            if self._game_running():
                raise ModPluginLoadingError('游戏正在运行，手动旧代理清理等待游戏完全退出。')
        try:
            if current.phase == 'running':
                if cleanup_legacy_proxy:
                    remove_legacy_game_proxy(game_directory=executable.parent, require_idle=require_idle)
                return ModPluginLoaderStartResult(current, self._native_workspace_root, native_workspace=self._native_workspace)
            prepared = prepare_native_loader_workspace(
                application_root=self._application_root, workspace_path=self._native_workspace_root,
                operation_guard=guard, game_running=self._game_running,
            )
            self._retain_native_workspace(prepared)
            if cleanup_legacy_proxy:
                remove_legacy_game_proxy(game_directory=executable.parent, require_idle=require_idle)
            self._require_load_allowed(scoped_guard)
            runtime = self._runtime.start(
                payload_path=payload, launcher_path=launcher, payload_load_mode='loadlibrary',
                launch_guard=lambda: self._require_load_allowed(scoped_guard),
            )
        # 部分准备失败也保留实际写入记录，不误报成功或丢失清理归属。
        except NativeComponentFilesPendingCleanup as error:
            self._retain_native_workspace(error.deployment)
            raise ModPluginLoadingPendingCleanup('Loader 准备未完成；已保留实际写入记录，等待清理。',
                                                 native_workspace=self._native_workspace) from error
        except (EquipmentPluginDeploymentError, ModLoaderRuntimeError, OSError) as error:
            raise ModPluginLoadingError(str(error)) from error
        self._active_payload_sha256 = self._native_workspace.managed_files[native_loader_payload_relative(self._application_root)]
        return ModPluginLoaderStartResult(runtime, self._native_workspace_root, native_workspace=self._native_workspace)

    def launcher_running(self, game_executable_path: str | Path) -> bool:
        return any(
            selected_launcher_running(candidate)
            for candidate in game_launcher_candidates(game_executable_path)
        )


    def stop_loader(
        self,
        *,
        timeout_ms: int = MOD_LOADER_STOP_TIMEOUT_MS,
    ) -> bool:
        try:
            stopped = self._runtime.stop(timeout_ms=timeout_ms)
            return stopped
        except (EquipmentPluginDeploymentError, ModLoaderRuntimeError) as exc:
            raise ModPluginLoadingError(str(exc)) from exc

    def close(self) -> None:
        self.stop_loader(timeout_ms=MOD_LOADER_STOP_TIMEOUT_MS)


    @staticmethod
    def _require_msvc_runtime() -> None:
        runtime = probe_mod_plugin_msvc_runtime()
        if runtime.get("ready"):
            return
        files = runtime.get("files")
        missing = (
            [name for name, exists in files.items() if not exists]
            if isinstance(files, dict)
            else list(MSVC_RUNTIME_FILES)
        )
        raise ModPluginLoadingError(
            "缺少 Microsoft Visual C++ 2015–2022 Redistributable x64："
            + "、".join(missing)
        )
