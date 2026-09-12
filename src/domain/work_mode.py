# 定义本机工作模式、执行权限与逐功能检测事实。
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WorkMode(str, Enum):
    OFFLINE = "offline"
    LOW = "low"
    MEDIUM = "medium"
    DEVELOPER = "developer"


class Capability(str, Enum):
    LOCAL = "local"
    PACKET_CAPTURE = "packet_capture"
    INTERFACE_INPUT = "interface_input"
    NATIVE_LOAD = "native_load"
    NATIVE_SYNC = "native_sync"
    NATIVE_BATTLE = "native_battle"
    NATIVE_EQUIPMENT = "native_equipment"
    DIAGNOSTICS = "diagnostics"
    COMPARE_SOURCES = "compare_sources"


NATIVE_CAPABILITIES = frozenset({
    Capability.NATIVE_LOAD, Capability.NATIVE_SYNC,
    Capability.NATIVE_BATTLE, Capability.NATIVE_EQUIPMENT,
})


@dataclass(frozen=True)
class WorkModeSettings:
    mode: WorkMode = WorkMode.OFFLINE
    risk_confirmed: bool = False
    paused: bool = False
    auto_sync_enabled: bool = True
    pending_cleanup: bool = True
    game_executable: str = ""
    # JSON text keeps the frozen settings genuinely immutable across callers.
    deployment_json: str = "{}"
    revision: int = 0


def allowed_capabilities(settings: WorkModeSettings) -> frozenset[Capability]:
    result = {Capability.LOCAL}
    if settings.mode == WorkMode.OFFLINE or not settings.risk_confirmed:
        return frozenset(result)
    result.add(Capability.INTERFACE_INPUT)
    if settings.mode == WorkMode.LOW:
        result.add(Capability.PACKET_CAPTURE)
    elif settings.mode == WorkMode.MEDIUM:
        result.update(NATIVE_CAPABILITIES)
    elif settings.mode == WorkMode.DEVELOPER:
        result.update({Capability.DIAGNOSTICS, Capability.PACKET_CAPTURE, Capability.COMPARE_SOURCES})
        result.update(NATIVE_CAPABILITIES)
    return frozenset(result)


class CheckState(str, Enum):
    AVAILABLE = "available"
    WAITING = "waiting"
    MISSING = "missing"
    FAULT = "fault"
    CLEANUP_PENDING = "cleanup_pending"


@dataclass(frozen=True)
class NativeFeatureProbe:
    files: bool | None = None
    pipe: bool | None = None
    handshake: bool | None = None
    supported: bool | None = None
    snapshot: bool | None = None
    fault: str = ""
    ready: bool | None = None
    reason: str = ""
    complete: bool | None = None
    source_coverage: str = "unknown"
    projection_complete: bool | None = None
    profile_projection_supported: bool | None = None


@dataclass(frozen=True)
class WorkModeProbe:
    analysis_available: bool | None = None
    component_update_state: CheckState | None = None
    component_update_detail: str = ""
    game_path_valid: bool | None = None
    game_running: bool = False
    logged_in: bool = False
    core_available: bool | None = None
    npcap_available: bool | None = None
    input_available: bool | None = None
    packet_listening: bool = False
    packet_snapshot: bool = False
    packet_fault: str = ""
    native_character: NativeFeatureProbe = NativeFeatureProbe()
    native_inventory: NativeFeatureProbe = NativeFeatureProbe()
    native_team: NativeFeatureProbe = NativeFeatureProbe()
    native_environment: NativeFeatureProbe = NativeFeatureProbe()
    native_battle: NativeFeatureProbe = NativeFeatureProbe()
    native_equipment: NativeFeatureProbe = NativeFeatureProbe()
    native_load: NativeFeatureProbe = NativeFeatureProbe()
    cleanup_detail: str = ""


@dataclass(frozen=True)
class FeatureCheck:
    feature: str
    label: str
    state: CheckState
    detail: str
    actions: tuple[str, ...] = ()
    facts: tuple[tuple[str, bool | str | None], ...] = ()


@dataclass(frozen=True)
class WorkModeReport:
    mode: WorkMode
    features: tuple[FeatureCheck, ...]

    @property
    def ready(self) -> bool:
        return all(item.state == CheckState.AVAILABLE for item in self.features)
