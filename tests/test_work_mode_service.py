# 验证本机模式授权、持久化失败与逐功能检测边界。
import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from src.domain.work_mode import (
    Capability, CheckState, NativeFeatureProbe, WorkMode, WorkModeProbe,
)
from src.services.work_mode_service import WorkModeDenied, WorkModeService


def test_first_install_and_legacy_plugin_risk_do_not_grant_mode(tmp_path):
    path = tmp_path / "work_mode.json"
    service = WorkModeService(path)
    assert service.settings.mode == WorkMode.OFFLINE
    assert not service.settings.auto_sync_enabled
    assert not service.allowed("native_sync", automatic=True)
    assert not service.allowed("native_load", automatic=True)
    path.write_text(json.dumps({"plugin_risk_confirmed": True}), encoding="utf-8")
    service = WorkModeService(path)
    assert service.settings.mode == WorkMode.OFFLINE
    assert service.load_error
    assert service.allowed("local")
    assert not service.allowed("native_sync")


def test_old_enabled_preference_is_disabled_once_but_explicit_new_choice_survives(tmp_path):
    path = tmp_path / "work_mode.json"
    path.write_text(json.dumps({
        "schema_version": 1, "mode": "medium", "risk_confirmed": True,
        "auto_sync_enabled": True, "pending_cleanup": False, "revision": 7,
        "game_executable": "fixture.exe", "deployment": {"loading_method": "loader"},
    }), encoding="utf-8")
    service = WorkModeService(path)
    assert service.settings.mode == WorkMode.MEDIUM
    assert not service.settings.auto_sync_enabled
    assert not service.allowed("native_load", automatic=True)
    assert service.deployment_record == {"loading_method": "loader"}
    service.enable_auto_sync_after_preflight()
    assert service.settings.auto_sync_enabled
    assert service.allowed("native_load", automatic=True)
    restarted = WorkModeService(path)
    assert restarted.settings.auto_sync_enabled
    restarted.set_auto_sync_enabled(False)
    assert not WorkModeService(path).settings.auto_sync_enabled
    assert WorkModeService(path).allowed("native_load", automatic=True)


def test_preflight_resumes_confirmed_mode_pause_only_after_cleanup(tmp_path):
    service = WorkModeService(tmp_path / "work_mode.json")
    service.select_mode("medium", risk_confirmed=True)
    service.set_paused(True)
    with pytest.raises(WorkModeDenied):
        service.enable_auto_sync_after_preflight(resume_paused=True)
    assert service.settings.paused and not service.settings.auto_sync_enabled
    service.set_cleanup_pending(False)
    service.enable_auto_sync_after_preflight(resume_paused=True)
    assert service.settings.auto_sync_enabled and not service.settings.paused


@pytest.mark.parametrize("mode,packet,native,compare", [
    ("offline", False, False, False),
    ("low", True, False, False),
    ("medium", False, True, False),
    ("developer", True, True, True),
])
def test_mode_execution_matrix(tmp_path, mode, packet, native, compare):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode(mode, risk_confirmed=True)
    service.enable_auto_sync_after_preflight()
    assert service.allowed(Capability.PACKET_CAPTURE) == packet
    assert service.allowed(Capability.NATIVE_SYNC) == native
    assert service.allowed(Capability.NATIVE_LOAD) == native
    assert service.allowed(Capability.COMPARE_SOURCES) == compare
    assert service.allowed(Capability.INTERFACE_INPUT) == (mode != "offline")
    assert service.allowed(Capability.DIAGNOSTICS)
    for capability in Capability:
        expected = capability in {Capability.PACKET_CAPTURE, Capability.NATIVE_LOAD, Capability.NATIVE_SYNC}
        assert service.allowed(capability, automatic=True) == (service.allowed(capability) and expected)
    service.set_paused(True)
    assert not any(service.allowed(capability, automatic=True) for capability in Capability)


@pytest.mark.parametrize("source", ["dual", "native", "packet", "obsolete"])
@pytest.mark.parametrize("confirmed", [True, False])
def test_legacy_options_are_retired_without_losing_mode_or_deployment(tmp_path, source, confirmed):
    path = tmp_path / "settings.json"
    original = {
        "schema_version": 1, "mode": "developer", "risk_confirmed": confirmed,
        "developer_source": source, "auto_manage_components": False, "auto_sync": True,
        "paused": True, "pending_cleanup": False, "game_executable": "fixture.exe",
        "deployment": {"loading_method": "loader"}, "revision": 7,
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    before = path.read_bytes()
    service = WorkModeService(path)
    assert not service.load_error
    assert path.read_bytes() == before
    assert service.settings.mode == (WorkMode.DEVELOPER if confirmed else WorkMode.OFFLINE)
    assert service.allowed("packet_capture") is confirmed
    assert service.allowed("native_sync") is confirmed
    assert service.allowed("compare_sources") is confirmed
    assert service.settings.paused and not service.settings.pending_cleanup
    assert not service.settings.auto_sync_enabled
    assert not service.allowed("native_sync", automatic=True)
    assert service.settings.game_executable == "fixture.exe"
    assert service.deployment_record == original["deployment"]
    service.set_paused(False)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert not {"developer_source", "auto_manage_components", "auto_sync"}.intersection(saved)
    assert not service.allowed("native_sync", automatic=True)
    assert not service.allowed("native_load", automatic=True)
    assert saved["auto_sync_enabled"] is False
    assert saved["revision"] == 8 and saved["deployment"] == original["deployment"]
    assert WorkModeService(path).settings == service.settings


@pytest.mark.parametrize("mode,sync", [("low", "packet_capture"), ("medium", "native_sync"), ("developer", "native_sync")])
def test_auto_sync_switch_does_not_revoke_manual_capture_or_component_management(tmp_path, mode, sync):
    path = tmp_path / "settings.json"
    service = WorkModeService(path)
    service.select_mode(mode, risk_confirmed=True)
    service.enable_auto_sync_after_preflight()
    service.set_auto_sync_enabled(False)
    assert not service.allowed(sync, automatic=True)
    assert service.allowed(sync)
    assert service.allowed("packet_capture") == (mode != "medium")
    assert service.allowed("native_battle") == (mode != "low")
    assert service.allowed("native_load", automatic=True) == (mode != "low")
    assert not WorkModeService(path).settings.auto_sync_enabled
    service.set_auto_sync_enabled(True)
    assert service.allowed(sync, automatic=True)
    assert WorkModeService(path).settings.auto_sync_enabled


@pytest.mark.parametrize("paused,enabled", [(False, False), (False, True), (True, True)])
def test_explicit_auto_sync_preference_survives_restart(tmp_path, paused, enabled):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "schema_version": 1, "mode": "low", "risk_confirmed": True,
        "paused": paused, "auto_sync_enabled": enabled,
        "sync_guidance_version": 1, "component_auto_ready": True,
    }), encoding="utf-8")
    service = WorkModeService(path)
    assert service.settings.auto_sync_enabled is enabled
    assert service.allowed("packet_capture", automatic=True) == (enabled and not paused)


def test_auto_sync_enabling_never_confirms_mode_or_releases_safety_pause(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.set_auto_sync_enabled(True)
    assert service.settings.mode == WorkMode.OFFLINE
    assert not service.allowed("packet_capture", automatic=True)
    service.select_mode("medium", risk_confirmed=True)
    service.set_paused(True)
    service.set_auto_sync_enabled(True)
    assert service.settings.paused
    assert not service.allowed("native_sync", automatic=True)
    assert not service.allowed("native_load", automatic=True)
    with pytest.raises(WorkModeDenied):
        service.select_mode("medium")
    assert service.settings.paused
    service.select_mode("medium", risk_confirmed=True)
    assert not service.settings.paused
    assert service.allowed("native_sync", automatic=True)


def test_sync_preference_survives_mode_selection(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.set_auto_sync_enabled(False)
    service.select_mode("medium", risk_confirmed=True)
    assert not service.settings.auto_sync_enabled
    assert not service.allowed("native_load", automatic=True)
    assert not service.allowed("native_sync", automatic=True)


def test_failed_sync_disable_revokes_but_failed_enable_does_not_grant(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("low", risk_confirmed=True)
    with patch.object(service._store, "save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            service.set_auto_sync_enabled(False)
        assert not service.allowed("packet_capture", automatic=True)
        assert service.allowed("packet_capture")
        with pytest.raises(OSError):
            service.set_auto_sync_enabled(True)
    assert not service.settings.auto_sync_enabled
    service.set_auto_sync_enabled(False)
    service.set_auto_sync_enabled(True)
    assert service.allowed("packet_capture", automatic=True)


@pytest.mark.parametrize("mode", ["developer", "low"])
def test_failed_mode_reconfirmation_does_not_release_safety_pause(tmp_path, mode):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("developer", risk_confirmed=True)
    service.set_paused(True)
    with patch.object(service._store, "save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            service.select_mode(mode, risk_confirmed=True)
    assert service.settings.paused
    assert not any(service.allowed(capability, automatic=True) for capability in Capability)


def test_auto_sync_and_deployment_updates_do_not_invalidate_manual_operations(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("developer", risk_confirmed=True)
    generation = service.operation_revision
    service.set_auto_sync_enabled(False)
    service.update_deployment({"files": []})
    service.set_cleanup_pending(False)
    service.set_auto_sync_enabled(True)
    assert service.operation_revision == generation
    assert service.settings.revision > generation


def test_operation_revision_prevents_mode_switch_back_reviving_old_operations(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("developer", risk_confirmed=True)
    generation = service.operation_revision
    service.select_mode("low", risk_confirmed=True)
    service.select_mode("developer", risk_confirmed=True)
    assert service.operation_revision > generation
    generation = service.operation_revision
    service.set_paused(True)
    service.set_paused(False)
    assert service.operation_revision > generation
    generation = service.operation_revision
    service.set_game_executable("fixture.exe")
    assert service.operation_revision > generation


def test_failed_revocation_invalidates_operations_but_failed_upgrade_does_not(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    generation = service.operation_revision
    with patch.object(service._store, "save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            service.select_mode("medium", risk_confirmed=True)
    assert service.operation_revision == generation
    service.select_mode("medium", risk_confirmed=True)
    generation = service.operation_revision
    with patch.object(service._store, "save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            service.select_mode("offline")
    assert service.operation_revision > generation


def test_each_risk_selection_requires_explicit_confirmation(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("medium", risk_confirmed=True)
    with pytest.raises(WorkModeDenied):
        service.select_mode("developer")
    assert service.settings.mode == WorkMode.MEDIUM


def test_guided_enable_allows_sync_and_pause_and_cleanup_persist(tmp_path):
    path = tmp_path / "settings.json"
    service = WorkModeService(path)
    service.select_mode("medium", risk_confirmed=True)
    assert not service.allowed("native_load", automatic=True)
    assert not service.allowed("native_sync", automatic=True)
    service.enable_auto_sync_after_preflight()
    assert service.allowed("native_load")
    assert service.allowed("native_load", automatic=True)
    assert service.allowed("native_sync", automatic=True)
    assert not service.allowed("native_battle", automatic=True)
    service.set_paused(True)
    restarted = WorkModeService(path)
    assert not restarted.allowed("native_sync", automatic=True)
    assert restarted.allowed("native_sync")
    restarted.set_cleanup_pending(False)
    restarted.select_mode("low", risk_confirmed=True)
    assert restarted.settings.pending_cleanup
    assert not restarted.allowed("native_load")


def test_failed_upgrade_never_grants_and_failed_downgrade_revokes(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    with patch.object(service._store, "save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            service.select_mode("medium", risk_confirmed=True)
    assert service.settings.mode == WorkMode.OFFLINE
    service.select_mode("medium", risk_confirmed=True)
    with patch.object(service._store, "save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            service.select_mode("offline")
    assert service.settings.mode == WorkMode.OFFLINE
    assert not service.allowed("native_sync")


def test_failed_pause_persistence_still_revokes_automatic_actions(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("developer", risk_confirmed=True)
    with patch.object(service._store, "save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            service.set_paused(True)
    assert service.settings.paused
    assert not any(service.allowed(capability, automatic=True) for capability in Capability)
    assert service.allowed("native_sync") and service.allowed("packet_capture")


def test_deployment_facts_are_detached_from_authority(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    original = {"files": [{"name": "test.dll", "sha256": "abc"}]}
    service.update_deployment(original)
    original["files"].clear()
    copy = service.deployment_record
    copy["files"].clear()
    assert len(service.deployment_record["files"]) == 1
    assert service.settings.mode == WorkMode.OFFLINE


def test_manual_components_and_independent_feature_states(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("medium", risk_confirmed=True)
    service.set_cleanup_pending(False)
    ready = NativeFeatureProbe(True, True, True, True, True, ready=True, complete=True, source_coverage="complete")
    probe = WorkModeProbe(
        game_path_valid=True, game_running=True, logged_in=True,
        core_available=True, input_available=True, native_load=ready,
        native_inventory=ready, native_equipment=replace(ready, snapshot=False),
        native_battle=replace(ready, supported=False),
    )
    checks = {item.feature: item for item in service.build_report(probe).features}
    assert checks["native_inventory"].state == CheckState.AVAILABLE
    assert checks["native_battle"].state == CheckState.MISSING
    assert checks["native_equipment"].state == CheckState.WAITING
    assert "component_update" in checks
    assert all("enable_auto_manage" not in item.actions for item in checks.values())
    assert "packet_capture" not in checks
    assert dict(checks["native_equipment"].facts)["snapshot"] is False


@pytest.mark.parametrize("native,state", [
    (NativeFeatureProbe(files=False), CheckState.MISSING),
    (NativeFeatureProbe(files=True, pipe=False), CheckState.WAITING),
    (NativeFeatureProbe(files=True, pipe=True, handshake=None), CheckState.WAITING),
    (NativeFeatureProbe(files=True, pipe=True, handshake=False), CheckState.FAULT),
    (NativeFeatureProbe(True, True, True, True, False), CheckState.WAITING),
])
def test_native_stages_not_collapsed(tmp_path, native, state):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("medium", risk_confirmed=True)
    report = service.build_report(WorkModeProbe(
        game_path_valid=True, game_running=True, logged_in=True,
        core_available=True, native_inventory=native,
    ))
    assert next(item for item in report.features if item.feature == "native_inventory").state == state


@pytest.mark.parametrize("mode, changes, available", [
    ("developer", {}, True),
    ("medium", {}, False),
    ("developer", {"handshake": False}, False),
    ("developer", {"pipe": False}, False),
    ("developer", {"supported": False}, False),
    ("developer", {"ready": False}, False),
])
def test_external_native_provider_requires_developer_handshake_and_readiness(tmp_path, mode, changes, available):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode(mode, risk_confirmed=True)
    service.set_cleanup_pending(False)
    native = replace(NativeFeatureProbe(files=False, pipe=True, handshake=True,
                                       supported=True, ready=True), **changes)
    probe = WorkModeProbe(game_path_valid=True, game_running=True, core_available=True,
                         native_load=native, native_battle=native)
    checks = {item.feature: item for item in service.build_report(probe).features}
    assert (checks["native_battle"].state == CheckState.AVAILABLE) is available
    assert dict(checks["native_battle"].facts)["files"] is False
    assert checks["native_load"].state == CheckState.MISSING


def test_external_native_provider_still_requires_complete_inventory(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("developer", risk_confirmed=True)
    service.set_cleanup_pending(False)
    native = NativeFeatureProbe(files=False, pipe=True, handshake=True, supported=True,
                                ready=True, snapshot=True, complete=False, source_coverage="partial")
    probe = WorkModeProbe(game_path_valid=True, game_running=True, core_available=True,
                         native_inventory=native)
    checks = {item.feature: item for item in service.build_report(probe).features}
    assert checks["native_inventory"].state == CheckState.MISSING


def test_supported_battle_still_needs_business_readiness(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("medium", risk_confirmed=True)
    probe = WorkModeProbe(
        game_path_valid=True, game_running=True, logged_in=True, core_available=True,
        native_battle=NativeFeatureProbe(True, True, True, True, ready=False, reason="正在初始化战报事件"),
    )
    report = service.build_report(probe)
    check = next(item for item in report.features if item.feature == "native_battle")
    assert check.state == CheckState.MISSING
    assert "初始化" in check.detail


def test_partial_native_snapshot_is_missing_not_infinite_wait(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("medium", risk_confirmed=True)
    probe = WorkModeProbe(
        game_path_valid=True, game_running=True, logged_in=True, core_available=True,
        native_inventory=NativeFeatureProbe(True, True, True, True, True, ready=True, complete=False),
    )
    report = service.build_report(probe)
    check = next(item for item in report.features if item.feature == "native_inventory")
    assert check.state == CheckState.MISSING
    assert "部分观察" in check.detail


def test_packet_listening_waits_for_full_login_snapshot(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("low", risk_confirmed=True)
    probe = WorkModeProbe(
        game_running=True, logged_in=True, core_available=True,
        npcap_available=True, packet_listening=True, packet_snapshot=False,
    )
    report = service.build_report(probe)
    check = next(item for item in report.features if item.feature == "packet_capture")
    assert check.state == CheckState.WAITING
    assert "完整" in check.detail


def test_atomic_replace_failure_preserves_previous_readable_settings(tmp_path):
    path = tmp_path / "settings.json"
    service = WorkModeService(path)
    service.select_mode("low", risk_confirmed=True)
    service.set_paused(True)
    previous = path.read_bytes()
    with patch("src.integrations.work_mode_settings.os.replace", side_effect=OSError("locked")):
        with pytest.raises(OSError):
            service.set_paused(False)
    assert path.read_bytes() == previous
    assert service.settings.paused
    assert not list(tmp_path.glob(".work-mode-*.tmp"))
    service.set_paused(False)
    assert not WorkModeService(path).settings.paused


def test_failed_mixed_transition_cannot_introduce_new_source(tmp_path):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("medium", risk_confirmed=True)
    with patch.object(service._store, "save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            service.select_mode("low", risk_confirmed=True)
    assert not service.allowed("native_sync")
    assert not service.allowed("packet_capture")
    assert service.settings.pending_cleanup


def test_corrupt_utf8_settings_fall_back_to_offline(tmp_path):
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xff\xfe\x00")
    service = WorkModeService(path)
    assert service.settings.mode == WorkMode.OFFLINE
    assert service.load_error


@pytest.mark.parametrize("reason,state", [
    ("scene_transition", CheckState.WAITING), ("pawn_unavailable", CheckState.WAITING),
    ("sdk_unavailable", CheckState.FAULT), ("hook_unavailable", CheckState.FAULT),
])
def test_native_readiness_reason_classification(tmp_path, reason, state):
    service = WorkModeService(tmp_path / "settings.json")
    service.select_mode("medium", risk_confirmed=True)
    probe = WorkModeProbe(
        game_path_valid=True, game_running=True, logged_in=True, core_available=True,
        native_battle=NativeFeatureProbe(True, True, True, True, ready=False, reason=reason),
    )
    report = service.build_report(probe)
    assert next(item for item in report.features if item.feature == "native_battle").state == state
