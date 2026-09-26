# 编排本机工作模式授权、暂停和清理待办的持久化。
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Any

from src.domain.work_mode import (
    Capability, WorkMode, WorkModeProbe, WorkModeReport,
    WorkModeSettings, allowed_capabilities,
)
from src.integrations.work_mode_settings import WorkModeSettingsStore
from src.services.work_mode_checks import build_work_mode_report


class WorkModeDenied(PermissionError):
    pass


class WorkModeService:
    def __init__(self, settings_path: Path) -> None:
        self._store = WorkModeSettingsStore(settings_path)
        self._settings = self._store.load()
        self._operation_revision = 0
        self._lock = RLock()

    @property
    def settings(self) -> WorkModeSettings:
        return self._settings

    @property
    def load_error(self) -> str:
        return self._store.load_error

    @property
    def operation_revision(self) -> int:
        return self._operation_revision

    @property
    def deployment_record(self) -> dict[str, Any]:
        return json.loads(self._settings.deployment_json)

    def _save(self, candidate: WorkModeSettings, *, revoke_first: bool = False) -> None:
        candidate = replace(candidate, revision=self._settings.revision + 1)
        if revoke_first:
            self._set_settings(candidate)
        self._store.save(candidate)
        self._set_settings(candidate)

    def _set_settings(self, candidate: WorkModeSettings) -> None:
        previous = self._settings
        if (previous.mode, previous.risk_confirmed, previous.paused, previous.game_executable) != (
            candidate.mode, candidate.risk_confirmed, candidate.paused, candidate.game_executable,
        ):
            self._operation_revision += 1
        self._settings = candidate

    def select_mode(
        self, mode: WorkMode | str, *, risk_confirmed: bool = False,
    ) -> WorkModeSettings:
        mode = WorkMode(mode)
        if mode != WorkMode.OFFLINE and risk_confirmed is not True:
            raise WorkModeDenied("请先明确同意所选工作模式的风险。")
        with self._lock:
            candidate = replace(
                self._settings, mode=mode, risk_confirmed=mode != WorkMode.OFFLINE,
                paused=False,
                pending_cleanup=self._settings.pending_cleanup or mode in {
                    WorkMode.OFFLINE, WorkMode.LOW,
                },
            )
            old_allowed = allowed_capabilities(self._settings)
            new_allowed = allowed_capabilities(candidate)
            # On failed mixed transitions retain only the shared authority.
            if not old_allowed.issubset(new_allowed):
                safe = replace(candidate, paused=self._settings.paused) if new_allowed.issubset(old_allowed) else replace(
                    self._settings, mode=WorkMode.OFFLINE, risk_confirmed=False,
                    pending_cleanup=True,
                )
                self._set_settings(safe)
            self._save(candidate)
            return self._settings

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._save(replace(self._settings, paused=bool(paused)), revoke_first=paused)

    def set_auto_sync_enabled(self, enabled: bool) -> None:
        with self._lock:
            enabled = bool(enabled)
            self._save(
                replace(self._settings, auto_sync_enabled=enabled), revoke_first=not enabled,
            )

    def enable_auto_sync_after_preflight(self, *, resume_paused: bool = False) -> None:
        """Commit both opt-ins together after the visible preparation step."""
        with self._lock:
            if resume_paused and (not self._settings.risk_confirmed or self._settings.pending_cleanup):
                raise WorkModeDenied("工作模式未确认或组件仍待清理，不能恢复同步。")
            self._save(replace(
                self._settings, paused=False if resume_paused else self._settings.paused,
                auto_sync_enabled=True, component_auto_ready=True,
            ))

    def set_component_auto_ready(self, ready: bool) -> None:
        with self._lock:
            self._save(replace(self._settings, component_auto_ready=bool(ready)), revoke_first=not ready)

    def set_cleanup_pending(self, pending: bool) -> None:
        with self._lock:
            self._save(replace(self._settings, pending_cleanup=bool(pending)), revoke_first=pending)

    def set_game_executable(self, path: str) -> None:
        with self._lock:
            self._save(replace(self._settings, game_executable=str(path)))

    def update_deployment(self, record: dict[str, Any]) -> None:
        if not isinstance(record, dict):
            raise TypeError("deployment record must be a mapping")
        with self._lock:
            self._save(replace(
                self._settings, deployment_json=json.dumps(record, ensure_ascii=False),
            ))

    def allowed(self, capability: Capability | str, *, automatic: bool = False) -> bool:
        capability = Capability(capability)
        settings = self._settings
        if capability not in allowed_capabilities(settings):
            return False
        if not automatic:
            return True
        if settings.paused:
            return False
        if capability in {Capability.PACKET_CAPTURE, Capability.NATIVE_SYNC}:
            return settings.auto_sync_enabled
        # Automatic synchronization never starts battle recording or input actions.
        return capability == Capability.NATIVE_LOAD and settings.component_auto_ready

    def require(self, capability: Capability | str, *, automatic: bool = False) -> None:
        if not self.allowed(capability, automatic=automatic):
            raise WorkModeDenied("当前工作模式或暂停设置不允许此操作，请检查工作模式设置。")

    def build_report(self, probe: WorkModeProbe) -> WorkModeReport:
        return build_work_mode_report(self._settings, probe)
