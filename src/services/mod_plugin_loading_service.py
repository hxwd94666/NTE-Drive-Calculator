# 编排代理 DLL 与备用 Mod Loader 的互斥加载方式。
"""Application service for the optional game-side Mods Plugin."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
from typing import Protocol

from src.integrations.mod_loader import (
    MOD_LOADER_STOP_TIMEOUT_MS,
    ModLoaderRuntime,
    ModLoaderRuntimeError,
    ModLoaderRuntimeSnapshot,
    game_launcher_executable,
)
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    PLUGIN_FILENAME,
    game_executable,
    mod_workspace_registry_snapshot,
    packaged_plugin_dll,
    prepare_mod_workspace,
    restore_mod_workspace,
)


class ModPluginLoadingError(RuntimeError):
    """The requested loading-method transition is unsafe or unavailable."""


MSVC_RUNTIME_FILES = (
    "MSVCP140.dll",
    "VCRUNTIME140.dll",
    "VCRUNTIME140_1.dll",
)


def probe_mod_plugin_msvc_runtime() -> dict[str, object]:
    """Inspect the x64 runtime imported by the official 0.4.1 plugin."""

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
    ) -> ModLoaderRuntimeSnapshot: ...

    def stop(self, *, timeout_ms: int = MOD_LOADER_STOP_TIMEOUT_MS) -> bool: ...

    def close(self) -> None: ...


class ModPluginLoadingService:
    """Keep proxy deployment and managed Loader sessions mutually exclusive."""

    def __init__(
        self,
        *,
        application_root: str | Path,
        runtime: ModLoaderRuntimeContract | None = None,
    ) -> None:
        self._application_root = Path(application_root).resolve()
        self._runtime = runtime or ModLoaderRuntime(
            application_root=self._application_root
        )
        self._workspace_registration: _WorkspaceRegistrationSession | None = None

    def snapshot(self) -> ModLoaderRuntimeSnapshot:
        try:
            payload = packaged_plugin_dll(self._application_root)
            return self._runtime.snapshot(payload_path=payload)
        except (EquipmentPluginDeploymentError, ModLoaderRuntimeError) as exc:
            raise ModPluginLoadingError(str(exc)) from exc

    def ensure_proxy_deployment_allowed(self) -> None:
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
    ) -> ModPluginLoaderStartResult:
        executable = game_executable(game_executable_path)
        proxy = executable.parent / PLUGIN_FILENAME
        self._require_msvc_runtime()
        payload = packaged_plugin_dll(self._application_root)
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
            )
            removed_proxy = self._remove_proxy_for_loader(
                proxy_path=proxy,
                payload_path=payload,
                backup_directory=Path(proxy_backup_directory),
                recorded_proxy_sha256=recorded_proxy_sha256,
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
            runtime = self._runtime.start(
                payload_path=payload,
                launcher_path=launcher,
            )
        except (
            EquipmentPluginDeploymentError,
            ModLoaderRuntimeError,
            ModPluginLoadingError,
        ) as exc:
            proxy_rollback_error = self._rollback_removed_proxy(
                proxy_path=proxy,
                removal=removed_proxy,
            )
            rollback_error = self._restore_workspace_registration(
                workspace_path=workspace,
                previous_value=observed_previous_value,
                previous_value_existed=observed_previous_exists,
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
        return ModPluginLoaderStartResult(
            runtime=runtime,
            workspace_path=workspace,
            removed_proxy=(
                removed_proxy.result if removed_proxy is not None else None
            ),
        )

    @staticmethod
    def _remove_proxy_for_loader(
        *,
        proxy_path: Path,
        payload_path: Path,
        backup_directory: Path,
        recorded_proxy_sha256: str,
    ) -> _PreparedProxyRemoval | None:
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
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup_root = backup_directory.expanduser().resolve()
            backup_path = backup_root / (
                f"{proxy_path.parent.name}_{PLUGIN_FILENAME}."
                f"{timestamp}.{proxy_sha256[:16]}.bak"
            )
            try:
                backup_root.mkdir(parents=True, exist_ok=True)
                shutil.copy2(proxy_path, backup_path)
                if ModPluginLoadingService._file_sha256(backup_path) != proxy_sha256:
                    backup_path.unlink(missing_ok=True)
                    raise ModPluginLoadingError(
                        "未知 dwmapi.dll 的备份校验失败，已取消启动 Loader"
                    )
                if ModPluginLoadingService._file_sha256(proxy_path) != proxy_sha256:
                    raise ModPluginLoadingError(
                        "备份期间游戏目录中的 dwmapi.dll 已发生变化，已取消启动 Loader"
                    )
            except ModPluginLoadingError:
                raise
            except OSError as exc:
                raise ModPluginLoadingError(
                    "无法把未知 dwmapi.dll 备份到当前账号存储目录，已取消启动 Loader"
                ) from exc
        try:
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

    @staticmethod
    def _rollback_removed_proxy(
        *,
        proxy_path: Path,
        removal: _PreparedProxyRemoval | None,
    ) -> str | None:
        if removal is None:
            return None
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
            self._restore_owned_workspace_registration()
            return stopped
        except (EquipmentPluginDeploymentError, ModLoaderRuntimeError) as exc:
            raise ModPluginLoadingError(str(exc)) from exc

    def close(self) -> None:
        self.stop_loader(timeout_ms=MOD_LOADER_STOP_TIMEOUT_MS)

    def _restore_owned_workspace_registration(self) -> bool:
        session = self._workspace_registration
        if session is None:
            return False
        restored = restore_mod_workspace(
            workspace_path=session.workspace_path,
            previous_value=session.previous_value,
            previous_value_existed=session.previous_value_existed,
        )
        self._workspace_registration = None
        return restored

    @staticmethod
    def _restore_workspace_registration(
        *,
        workspace_path: Path | None,
        previous_value: str | None,
        previous_value_existed: bool,
    ) -> EquipmentPluginDeploymentError | None:
        if workspace_path is None:
            return None
        try:
            restore_mod_workspace(
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
