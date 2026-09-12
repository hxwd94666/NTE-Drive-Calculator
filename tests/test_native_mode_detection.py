# 用跨组件合成协议核对逐域检测，禁止把部分观察或旧库存当成就绪。
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from src.domain.work_mode import CheckState, NativeFeatureProbe, WorkModeProbe
from src.services.work_mode_runtime import WorkModeRuntime
from src.services.work_mode_service import WorkModeService


@pytest.mark.parametrize("domain_error", [None, "not_ready", "source_changed"])
@pytest.mark.parametrize("producer", [False, True])
def test_partial_native_fixture_does_not_block_ready_battle(tmp_path, domain_error, producer):
    fixture = json.loads((Path(__file__).parent / "fixtures/native_snapshot_222.json").read_text(encoding="utf-8"))
    assert fixture["synthetic"] is True
    status = fixture["steps"][0]["response"]["result"]
    character = (fixture["producerSteps"][0] if producer else fixture["steps"][1])["response"]["result"]
    domains = [character if item["domain"] == "character" else item for item in status["domains"]]
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    policy.set_cleanup_pending(False)
    game = tmp_path / "HTGame.exe"
    game.write_bytes(b"synthetic game marker")
    policy.set_game_executable(str(game))
    dll_caps = frozenset(fixture["nativeHandshake"]["result"]["capabilities"])
    core_caps = ["native_hit_buff_v1", "native_context_observation_v1", "battle_axis_v1",
                 "character.snapshot.v1", "inventory.snapshot.v1", "native_inventory_dto_v1", "team.snapshot.v1", "environment.snapshot.v1"]
    native = SimpleNamespace(battle_active=False, close=MagicMock(), inspect=MagicMock(return_value={
        "hello": {"capabilities": core_caps}, "domains": {"domains": domains},
        "status": {"native_status": {"ready": True, "readyReason": "ready"}},
        "domain_errors": {"inventory": {"code": -32001, "message": domain_error, "reason": ""}} if domain_error else {},
    }))
    runtime = WorkModeRuntime(policy=policy, native_session=native,
        loader=SimpleNamespace(), application_root=tmp_path, config_dir=tmp_path, game_running=lambda: True)
    runtime._discovered = True
    runtime._bundle = SimpleNamespace(ready=False, issues=("bundle missing",))
    runtime._deployed = SimpleNamespace(compatible=True, workspace_matches_package=False,
        proxy_matches_package=True, workspace_registered=True, native_capabilities=dll_caps, equipment_script_valid=True)
    with (patch.object(runtime, "_inspect_component_files"),
          patch("src.services.work_mode_runtime.resolve_nte_core_executable", return_value=game),
          patch("src.services.work_mode_runtime.create_bundled_analysis_client", return_value=None),
          patch("src.services.work_mode_runtime.native_capture_game_pid", return_value=1234),
          patch("src.services.work_mode_runtime.probe_equipment_pipe", return_value={"state": "available"}),
          patch("src.services.work_mode_runtime.npcap_installation_present", return_value=False)):
        probe = runtime.tick(allow_connect=True)
    checks = {row.feature: row for row in policy.build_report(probe).features}
    assert checks["native_battle"].state == CheckState.AVAILABLE
    assert checks["native_character"].state == CheckState.MISSING
    assert checks["native_inventory"].state != CheckState.AVAILABLE
    if domain_error:
        assert checks["native_inventory"].state == CheckState.WAITING
        assert probe.native_inventory.handshake is True
        assert not probe.native_inventory.fault
    assert checks["history_analysis"].state == CheckState.MISSING
    assert "packet_capture" not in checks
    native.inspect.assert_called_once_with(refresh=True, check_equipment=True)


def test_ready_domains_do_not_depend_on_battle_login_projection(tmp_path):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    ready = NativeFeatureProbe(True, True, True, True, True, ready=True, complete=True, source_coverage="complete")
    probe = WorkModeProbe(game_path_valid=True, game_running=True, core_available=True,
        logged_in=False, native_character=ready,
        native_battle=replace(ready, ready=False, reason="scene_transition"))
    checks = {row.feature: row for row in policy.build_report(probe).features}
    assert checks["native_character"].state == CheckState.AVAILABLE
    assert checks["native_battle"].state == CheckState.WAITING


def test_manual_compatible_components_do_not_require_auto_bundle(tmp_path):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    probe = WorkModeProbe(game_path_valid=True, native_load=NativeFeatureProbe(files=True))
    checks = {row.feature: row for row in policy.build_report(probe).features}
    assert checks["native_load"].state == CheckState.AVAILABLE
    assert checks["component_update"].state == CheckState.WAITING
    probe = replace(probe, component_update_state=CheckState.MISSING, component_update_detail="未提供整套清单")
    checks = {row.feature: row for row in policy.build_report(probe).features}
    assert checks["native_load"].state == CheckState.AVAILABLE
    assert checks["component_update"].state == CheckState.MISSING


def test_formal_inventory_proof_is_distinct_from_raw_coverage(tmp_path):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    native = NativeFeatureProbe(files=True, pipe=True, handshake=True, supported=True,
                                snapshot=True, ready=True, complete=False, source_coverage="unknown",
                                projection_complete=True)
    probe = WorkModeProbe(game_path_valid=True, core_available=True, game_running=True,
                          native_inventory=native)
    check = next(row for row in policy.build_report(probe).features if row.feature == "native_inventory")
    assert check.state == CheckState.AVAILABLE
    assert dict(check.facts)["complete"] is False and dict(check.facts)["source_coverage"] == "unknown"
    stale = replace(probe, native_inventory=replace(native, projection_complete=False))
    assert next(row for row in policy.build_report(stale).features if row.feature == "native_inventory").state == CheckState.MISSING


def test_supported_sparse_character_fields_do_not_claim_full_character_observation(tmp_path):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    native = NativeFeatureProbe(files=True, pipe=True, handshake=True, supported=True,
                                snapshot=True, ready=True, complete=False, source_coverage="unknown",
                                profile_projection_supported=True)
    probe = WorkModeProbe(game_path_valid=True, core_available=True, game_running=True,
                          native_character=native)
    check = next(row for row in policy.build_report(probe).features if row.feature == "native_character")
    assert check.state == CheckState.AVAILABLE and "未观测字段保持原值" in check.detail
    assert dict(check.facts)["complete"] is False
