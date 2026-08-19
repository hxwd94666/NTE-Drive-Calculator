# 编排代理 DLL 与备用 Mod Loader 的互斥加载方式。
"""Application service for the optional game-side Mods Plugin."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Protocol

from src.integrations.mod_loader import (
    MOD_LOADER_STOP_TIMEOUT_MS,
    ModLoaderRuntime,
    ModLoaderRuntimeError,
    ModLoaderRuntimeSnapshot,
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
class ModPluginLoaderStartResult:
    runtime: ModLoaderRuntimeSnapshot
    workspace_path: Path


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
    ) -> ModPluginLoaderStartResult:
        executable = game_executable(game_executable_path)
        proxy = executable.parent / PLUGIN_FILENAME
        if proxy.exists():
            raise ModPluginLoadingError(
                "游戏目录仍存在 dwmapi.dll。请先切换到代理方式执行“还原游戏目录”，"
                "确认代理 DLL 已移除后再启动备用 Loader。"
            )
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
            previous_exists, previous_value = mod_workspace_registry_snapshot()
        except EquipmentPluginDeploymentError as exc:
            raise ModPluginLoadingError(str(exc)) from exc
        workspace: Path | None = None
        try:
            workspace = prepare_mod_workspace(
                application_root=self._application_root,
                writable_workspace_path=destination,
                register_workspace=True,
            )
            runtime = self._runtime.start(payload_path=payload)
        except (EquipmentPluginDeploymentError, ModLoaderRuntimeError) as exc:
            rollback_error = self._restore_workspace_registration(
                workspace_path=workspace,
                previous_value=previous_value,
                previous_value_existed=previous_exists,
            )
            if rollback_error is not None:
                raise ModPluginLoadingError(
                    f"{exc}；且 Mod 工作区注册表回滚失败：{rollback_error}"
                ) from rollback_error
            raise ModPluginLoadingError(str(exc)) from exc
        self._workspace_registration = _WorkspaceRegistrationSession(
            workspace_path=workspace,
            previous_value=previous_value,
            previous_value_existed=previous_exists,
        )
        return ModPluginLoaderStartResult(runtime=runtime, workspace_path=workspace)

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
