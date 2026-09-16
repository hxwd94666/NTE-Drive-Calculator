# 编排代理 DLL 与备用 Mod Loader 的互斥加载方式。
"""Application service for the optional game-side Mods Plugin."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
from typing import Callable, Protocol

from src.integrations.mod_loader import (
    MOD_LOADER_STOP_TIMEOUT_MS,
    ModLoaderRuntime,
    ModLoaderRuntimeError,
    ModLoaderRuntimeSnapshot,
    game_launcher_executable,
)
from src.integrations.operation_guard import require_operation
from src.integrations.legacy_game_proxy import remove_legacy_game_proxy
from src.integrations.game_component_bundle import inspect_game_component_bundle
from src.integrations.native_plugin_bundle import NATIVE_PLUGIN_LAYOUT
from src.services.native_loader_workspace import (
    NATIVE_LOADER_PAYLOAD_RELATIVE_PATH, inspect_native_loader_workspace, prepare_native_loader_workspace,
)
from src.services.native_plugin_deployment import (
    NativeComponentFilesDeployment, NativeComponentFilesPendingCleanup,
    NativePluginCleanupResult, cleanup_native_component_files,
)
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    PLUGIN_FILENAME,
    cleanup_mod_workspace,
    game_executable,
    game_process_running,
    mod_workspace_registry_snapshot,
    packaged_plugin_dll,
    prepare_mod_workspace,
    rollback_mod_workspace,
)


class ModPluginLoadingError(RuntimeError):
    """The requested loading-method transition is unsafe or unavailable."""


class ModPluginLoadingPendingCleanup(ModPluginLoadingError):
    def __init__(self, message: str, *, native_workspace: NativeComponentFilesDeployment):
        super().__init__(message)
        self.native_workspace = native_workspace


MSVC_RUNTIME_FILES = (
    "MSVCP140.dll",
    "MSVCP140_ATOMIC_WAIT.dll",
    "VCRUNTIME140.dll",
    "VCRUNTIME140_1.dll",
)


def probe_mod_plugin_msvc_runtime() -> dict[str, object]:
    """Inspect the x64 runtime required by the packaged capture components."""

    if os.name != "nt":
        return {"supported": False, "ready": False, "files": {}}
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    system_directory = system_root / "System32"
    files = {
        name: (system_directory / name).is_file()
        for name in MSVC_RUNTIME_FILES
    }
    return {
        "supported": True,
        "ready": all(files.values()),
        "directory": str(system_directory),
        "files": files,
    }


@dataclass(frozen=True)
class LocalProxyRemoval:
    sha256: str
    known: bool
    backup_path: Path | None


@dataclass(frozen=True)
class ModPluginLoaderStartResult:
    runtime: ModLoaderRuntimeSnapshot
    workspace_path: Path
    removed_proxy: LocalProxyRemoval | None = None
    native_workspace: NativeComponentFilesDeployment | None = None


@dataclass(frozen=True)
class _PreparedProxyRemoval:
    result: LocalProxyRemoval
    original_bytes: bytes | None


@dataclass(frozen=True)
class _WorkspaceRegistrationSession:
    workspace_path: Path
    previous_value: str | None
    previous_value_existed: bool


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
        payload_load_mode: str | None = None,
        launch_guard: Callable[[], None] | None = None,
    ) -> ModLoaderRuntimeSnapshot: ...

    def stop(self, *, timeout_ms: int = MOD_LOADER_STOP_TIMEOUT_MS) -> bool: ...

    def close(self) -> None: ...

    def require_payload_load_mode(self, mode: str) -> None: ...


class ModPluginLoadingService:
    """Keep proxy deployment and managed Loader sessions mutually exclusive."""

    def __init__(
        self,
        *,
        application_root: str | Path,
        runtime: ModLoaderRuntimeContract | None = None,
        operation_guard: Callable[[str], None] | None = None,
        game_running: Callable[[], bool] | None = None,
        native_workspace_path: str | Path | None = None,
    ) -> None:
        self._application_root = Path(application_root).resolve()
        self._runtime = runtime or ModLoaderRuntime(
            application_root=self._application_root
        )
        self._workspace_registration: _WorkspaceRegistrationSession | None = None
        self._operation_guard = operation_guard
        self._game_running = game_running or game_process_running
        self._active_payload_sha256 = ""
        self._native_workspace_root = Path(native_workspace_path or self._application_root / 'config/native-loader').expanduser().resolve()
        self._native_workspace: NativeComponentFilesDeployment | None = None

    @property
    def active_payload_sha256(self) -> str:
        """Hash frozen at this managed Loader start; not a current-file inference."""
        return self._active_payload_sha256

    @property
    def pending_workspace_cleanup_path(self) -> Path | None:
        """Expose the owned registration for application-level pending persistence."""
        session = self._workspace_registration
        return session.workspace_path if session is not None else None

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
        if (not isinstance(managed_files, dict) or not set(managed_files).issubset({NATIVE_LOADER_PAYLOAD_RELATIVE_PATH})
                or any(not isinstance(value, str) or len(value) != 64 or any(char not in '0123456789abcdef' for char in value)
                       for value in managed_files.values())):
            raise ModPluginLoadingError('Loader 运行目录记录包含无效文件或摘要。')
        self._native_workspace = NativeComponentFilesDeployment(
            directory, Path(backup_path).resolve() if backup_path else directory.parent / 'native-loader-backups',
            dict(managed_files),
        )

    def require_native_loader_supported(self) -> None:
        """Check support before removing an owned game entry; do not stage or launch."""
        self._require_load_allowed()
        bundle = inspect_game_component_bundle(self._application_root)
        if bundle.layout != NATIVE_PLUGIN_LAYOUT or not bundle.ready:
            raise ModPluginLoadingError('原生整包不可用：' + '；'.join(bundle.issues))
        self._require_msvc_runtime()
        try:
            self._runtime.require_payload_load_mode('loadlibrary')
        except ModLoaderRuntimeError as error:
            raise ModPluginLoadingError(str(error)) from error

    def _retain_native_workspace(self, record: NativeComponentFilesDeployment) -> None:
        previous = self._native_workspace
        managed = {}
        if previous is not None and previous.directory == record.directory:
            # Ownership is a recorded fact; cleanup separately verifies current hashes.
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
            if bundle.layout == NATIVE_PLUGIN_LAYOUT:
                if not bundle.ready:
                    raise ModPluginLoadingError('；'.join(bundle.issues))
                payload = self._native_workspace_root / NATIVE_LOADER_PAYLOAD_RELATIVE_PATH
            else:
                payload = packaged_plugin_dll(self._application_root)
            return self._runtime.snapshot(payload_path=payload)
        except (EquipmentPluginDeploymentError, ModLoaderRuntimeError) as exc:
            raise ModPluginLoadingError(str(exc)) from exc

    def ensure_proxy_deployment_allowed(self) -> None:
        self._require_load_allowed()
        snapshot = self.snapshot()
        if snapshot.phase == "running":
            raise ModPluginLoadingError(
                "Mod Loader 正在运行；请先切换到 Loader 方式并停止，再部署代理 DLL"
            )
        self._require_msvc_runtime()

    def start_loader(
        self,
        *,
        game_executable_path: str | Path,
        writable_workspace_path: str | Path,
        proxy_backup_directory: str | Path,
        recorded_proxy_sha256: str = "",
        recorded_proxy_workspace_path: str | Path | None = None,
        recorded_proxy_registry_value_before: str | None = None,
        recorded_proxy_registry_value_existed: bool = False,
        scoped_guard: Callable[[str], None] | None = None,
    ) -> ModPluginLoaderStartResult:
        self._require_load_allowed(scoped_guard)
        bundle = inspect_game_component_bundle(self._application_root)
        if bundle.layout == NATIVE_PLUGIN_LAYOUT:
            return self._start_native_loader(
                game_executable_path=game_executable_path, writable_workspace_path=writable_workspace_path,
                backup_directory=proxy_backup_directory, scoped_guard=scoped_guard,
            )

        def operation_guard(capability: str) -> None:
            require_operation(self._operation_guard, capability)
            if scoped_guard is not None:
                scoped_guard(capability)
        executable = game_executable(game_executable_path)
        proxy = executable.parent / PLUGIN_FILENAME
        self._require_msvc_runtime()
        payload = packaged_plugin_dll(self._application_root)
        if proxy.exists() and (
            proxy.is_symlink() or not proxy.is_file()
            or self._file_sha256(proxy) not in {
                self._file_sha256(payload), recorded_proxy_sha256.strip().casefold(),
            } - {""}
        ):
            raise ModPluginLoadingError("游戏目录中的 dwmapi.dll 归属未知或已修改，已取消启动 Loader。")
        destination = Path(writable_workspace_path).expanduser().resolve()
        current = self.snapshot()
        if current.phase == "running":
            workspace = (
                self._workspace_registration.workspace_path
                if self._workspace_registration is not None
                else destination
            )
            return ModPluginLoaderStartResult(
                runtime=current,
                workspace_path=workspace,
            )
        if current.phase != "stopped":
            raise ModPluginLoadingError(
                current.detail or f"Mod Loader 当前状态不可启动：{current.phase}"
            )
        try:
            launcher = game_launcher_executable(executable)
        except ModLoaderRuntimeError as exc:
            raise ModPluginLoadingError(str(exc)) from exc
        try:
            observed_previous_exists, observed_previous_value = (
                mod_workspace_registry_snapshot()
            )
        except EquipmentPluginDeploymentError as exc:
            raise ModPluginLoadingError(str(exc)) from exc
        session_previous_exists = observed_previous_exists
        session_previous_value = observed_previous_value
        workspace: Path | None = None
        removed_proxy: _PreparedProxyRemoval | None = None
        try:
            workspace = prepare_mod_workspace(
                application_root=self._application_root,
                writable_workspace_path=destination,
                register_workspace=True,
                game_running=self._game_running,
                operation_guard=operation_guard,
            )
            removed_proxy = self._remove_proxy_for_loader(
                proxy_path=proxy,
                payload_path=payload,
                backup_directory=Path(proxy_backup_directory),
                recorded_proxy_sha256=recorded_proxy_sha256,
                scoped_guard=scoped_guard,
            )
            if (
                removed_proxy is not None
                and self._removed_recorded_proxy(
                    removed_proxy.result,
                    recorded_proxy_sha256=recorded_proxy_sha256,
                )
                and self._registry_belongs_to_recorded_proxy(
                    current_exists=observed_previous_exists,
                    current_value=observed_previous_value,
                    recorded_workspace_path=recorded_proxy_workspace_path,
                )
            ):
                session_previous_exists = bool(
                    recorded_proxy_registry_value_existed
                )
                session_previous_value = recorded_proxy_registry_value_before
            payload_sha256 = self._file_sha256(payload)
            self._require_load_allowed(scoped_guard)
            runtime = self._runtime.start(
                payload_path=payload,
                launcher_path=launcher,
            )
        except (
            EquipmentPluginDeploymentError,
            ModLoaderRuntimeError,
            ModPluginLoadingError,
            PermissionError,
        ) as exc:
            proxy_rollback_error = self._rollback_removed_proxy(
                proxy_path=proxy,
                removal=removed_proxy,
                scoped_guard=scoped_guard,
            )
            rollback_error = self._restore_workspace_registration(
                workspace_path=workspace,
                previous_value=observed_previous_value,
                previous_value_existed=observed_previous_exists,
                scoped_guard=scoped_guard,
            )
            rollback_messages = []
            if proxy_rollback_error is not None:
                rollback_messages.append(
                    "游戏目录代理 DLL 回滚失败：" + proxy_rollback_error
                )
            if rollback_error is not None:
                rollback_messages.append(
                    "Mod 工作区注册表回滚失败：" + str(rollback_error)
                )
            if rollback_messages:
                raise ModPluginLoadingError(
                    f"{exc}；且" + "；".join(rollback_messages)
                ) from exc
            raise ModPluginLoadingError(str(exc)) from exc
        self._workspace_registration = _WorkspaceRegistrationSession(
            workspace_path=workspace,
            previous_value=session_previous_value,
            previous_value_existed=session_previous_exists,
        )
        self._active_payload_sha256 = payload_sha256
        return ModPluginLoaderStartResult(
            runtime=runtime,
            workspace_path=workspace,
            removed_proxy=(
                removed_proxy.result if removed_proxy is not None else None
            ),
        )

    def _start_native_loader(self, *, game_executable_path, writable_workspace_path, backup_directory, scoped_guard):
        if Path(writable_workspace_path).expanduser().resolve() != self._native_workspace_root:
            raise ModPluginLoadingError('原生 Loader 必须使用本机配置的专用运行目录。')
        self.require_native_loader_supported()
        self._require_load_allowed(scoped_guard)
        executable = game_executable(game_executable_path)
        launcher = game_launcher_executable(executable)
        payload = self._native_workspace_root / NATIVE_LOADER_PAYLOAD_RELATIVE_PATH
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
                raise ModPluginLoadingError('游戏正在运行，旧代理清理等待游戏完全退出。')
        try:
            if current.phase == 'running':
                remove_legacy_game_proxy(game_directory=executable.parent, require_idle=require_idle)
                return ModPluginLoaderStartResult(current, self._native_workspace_root, native_workspace=self._native_workspace)
            prepared = prepare_native_loader_workspace(
                application_root=self._application_root, workspace_path=self._native_workspace_root,
                backup_directory=backup_directory, operation_guard=guard, game_running=self._game_running,
            )
            self._retain_native_workspace(prepared)
            remove_legacy_game_proxy(game_directory=executable.parent, require_idle=require_idle)
            self._require_load_allowed(scoped_guard)
            runtime = self._runtime.start(
                payload_path=payload, launcher_path=launcher, payload_load_mode='loadlibrary',
                launch_guard=lambda: self._require_load_allowed(scoped_guard),
            )
        except NativeComponentFilesPendingCleanup as error:
            self._retain_native_workspace(error.deployment)
            raise ModPluginLoadingPendingCleanup('Loader 准备未完成；已保留实际写入记录，等待清理。',
                                                 native_workspace=self._native_workspace) from error
        except (EquipmentPluginDeploymentError, ModLoaderRuntimeError, OSError) as error:
            raise ModPluginLoadingError(str(error)) from error
        self._active_payload_sha256 = self._native_workspace.managed_files[NATIVE_LOADER_PAYLOAD_RELATIVE_PATH]
        return ModPluginLoaderStartResult(runtime, self._native_workspace_root, native_workspace=self._native_workspace)

    def _remove_proxy_for_loader(
        self,
        *,
        proxy_path: Path,
        payload_path: Path,
        backup_directory: Path,
        recorded_proxy_sha256: str,
        scoped_guard: Callable[[str], None] | None = None,
    ) -> _PreparedProxyRemoval | None:
        self._require_load_allowed(scoped_guard)
        if not proxy_path.exists():
            return None
        if not proxy_path.is_file():
            raise ModPluginLoadingError(
                "游戏目录中的 dwmapi.dll 不是普通文件，无法安全启动 Loader"
            )
        try:
            original_bytes = proxy_path.read_bytes()
        except OSError as exc:
            raise ModPluginLoadingError(
                "无法读取游戏目录中的 dwmapi.dll，请先关闭游戏和启动器"
            ) from exc
        proxy_sha256 = hashlib.sha256(original_bytes).hexdigest()
        try:
            payload_sha256 = ModPluginLoadingService._file_sha256(payload_path)
        except OSError as exc:
            raise ModPluginLoadingError(
                "无法读取打包的 Mod 插件，已取消启动 Loader"
            ) from exc
        normalized_recorded_hash = recorded_proxy_sha256.strip().casefold()
        known = proxy_sha256 in {
            payload_sha256,
            normalized_recorded_hash,
        } - {""}
        backup_path: Path | None = None
        if not known:
            raise ModPluginLoadingError("游戏目录中的 dwmapi.dll 归属未知或已修改，已取消启动 Loader。")
        try:
            self._require_load_allowed(scoped_guard)
            if ModPluginLoadingService._file_sha256(proxy_path) != proxy_sha256:
                raise ModPluginLoadingError(
                    "移除前游戏目录中的 dwmapi.dll 已发生变化，已取消启动 Loader"
                )
            proxy_path.unlink()
        except ModPluginLoadingError:
            raise
        except OSError as exc:
            raise ModPluginLoadingError(
                "无法移除游戏目录中的 dwmapi.dll，请先关闭游戏和启动器并检查目录权限"
            ) from exc
        return _PreparedProxyRemoval(
            result=LocalProxyRemoval(
                sha256=proxy_sha256,
                known=known,
                backup_path=backup_path,
            ),
            original_bytes=original_bytes if known else None,
        )

    def _rollback_removed_proxy(
        self,
        *,
        proxy_path: Path,
        removal: _PreparedProxyRemoval | None,
        scoped_guard: Callable[[str], None] | None = None,
    ) -> str | None:
        if removal is None:
            return None
        try:
            self._require_load_allowed(scoped_guard)
        except (PermissionError, ModPluginLoadingError, EquipmentPluginDeploymentError):
            return "加载授权已撤销或游戏已经启动，未恢复代理 DLL；请退出游戏后重新检测"
        try:
            if proxy_path.exists():
                if (
                    ModPluginLoadingService._file_sha256(proxy_path)
                    == removal.result.sha256
                ):
                    return None
                return "目标位置已出现另一个 dwmapi.dll，未覆盖该文件"
            if removal.result.backup_path is not None:
                shutil.copy2(removal.result.backup_path, proxy_path)
            elif removal.original_bytes is not None:
                proxy_path.write_bytes(removal.original_bytes)
            else:
                return "缺少可用于回滚的 DLL 内容"
            if ModPluginLoadingService._file_sha256(proxy_path) != removal.result.sha256:
                return "恢复后的 DLL 哈希校验失败"
        except OSError as exc:
            return f"{type(exc).__name__}"
        return None

    @staticmethod
    def _removed_recorded_proxy(
        removal: LocalProxyRemoval,
        *,
        recorded_proxy_sha256: str,
    ) -> bool:
        recorded = recorded_proxy_sha256.strip().casefold()
        return bool(recorded) and removal.sha256 == recorded

    @staticmethod
    def _registry_belongs_to_recorded_proxy(
        *,
        current_exists: bool,
        current_value: str | None,
        recorded_workspace_path: str | Path | None,
    ) -> bool:
        if not current_exists or not current_value or not recorded_workspace_path:
            return False
        current = os.path.normcase(os.path.abspath(current_value))
        recorded = os.path.normcase(os.path.abspath(str(recorded_workspace_path)))
        return current == recorded

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def stop_loader(
        self,
        *,
        timeout_ms: int = MOD_LOADER_STOP_TIMEOUT_MS,
    ) -> bool:
        try:
            stopped = self._runtime.stop(timeout_ms=timeout_ms)
            self.cleanup_workspace_registration()
            return stopped
        except (EquipmentPluginDeploymentError, ModLoaderRuntimeError) as exc:
            raise ModPluginLoadingError(str(exc)) from exc

    def close(self) -> None:
        self.stop_loader(timeout_ms=MOD_LOADER_STOP_TIMEOUT_MS)

    def cleanup_workspace_registration(self) -> bool:
        """Clear owned loading config after game exit; never restore historical config."""
        session = self._workspace_registration
        if session is None:
            return True
        if self._game_running():
            return False
        cleaned = cleanup_mod_workspace(
            workspace_path=session.workspace_path,
        )
        self._workspace_registration = None
        self._active_payload_sha256 = ""
        return cleaned

    def _restore_workspace_registration(
        self,
        *,
        workspace_path: Path | None,
        previous_value: str | None,
        previous_value_existed: bool,
        scoped_guard: Callable[[str], None] | None = None,
    ) -> EquipmentPluginDeploymentError | None:
        if workspace_path is None:
            return None
        try:
            self._require_load_allowed(scoped_guard)
        except (PermissionError, ModPluginLoadingError, EquipmentPluginDeploymentError):
            self._workspace_registration = _WorkspaceRegistrationSession(
                workspace_path, previous_value, previous_value_existed,
            )
            return EquipmentPluginDeploymentError("游戏已经启动，加载登记等待退出后清理。")
        try:
            rollback_mod_workspace(
                workspace_path=workspace_path,
                previous_value=previous_value,
                previous_value_existed=previous_value_existed,
            )
        except EquipmentPluginDeploymentError as exc:
            return exc
        return None

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
