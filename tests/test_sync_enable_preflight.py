# 验证开启同步的只读决策、进程等待和来源分支。
from dataclasses import replace

from src.domain.work_mode import NativeFeatureProbe, WorkMode, WorkModeProbe, WorkModeSettings
from src.services.sync_enable_preflight import decide_sync_enable


def test_low_mode_requires_npcap_but_not_game_path():
    settings = WorkModeSettings(mode=WorkMode.LOW, risk_confirmed=True)
    probe = WorkModeProbe(core_available=True, npcap_available=False)
    blocked = decide_sync_enable(settings, {}, probe)
    assert not blocked.ready and blocked.target == "npcap"
    ready = decide_sync_enable(settings, {}, replace(probe, npcap_available=True))
    assert ready.ready and ready.action_label is None


def test_d3d_waits_for_game_only_when_files_need_work():
    settings = WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True, pending_cleanup=False)
    probe = WorkModeProbe(game_path_valid=True, game_running=True, core_available=True,
                          native_load=NativeFeatureProbe(files=False))
    blocked = decide_sync_enable(settings, {}, probe)
    assert not blocked.ready and blocked.action_label == "前往部署组件"
    ready = decide_sync_enable(settings, {}, replace(
        probe, native_load=NativeFeatureProbe(files=True),
    ))
    assert ready.ready and ready.action_label is None


def test_loader_waits_for_selected_launcher_and_unknown_process_state():
    settings = WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True, pending_cleanup=False)
    probe = WorkModeProbe(game_path_valid=True, core_available=True,
                          native_load=NativeFeatureProbe(files=False))
    record = {"loading_method": "loader"}
    assert not decide_sync_enable(settings, record, probe).ready
    assert not decide_sync_enable(settings, record, replace(probe, launcher_running=True)).ready
    stopped = decide_sync_enable(settings, record, replace(probe, launcher_running=False))
    assert not stopped.ready and stopped.action_label == "前往部署组件"


def test_pending_cleanup_waits_for_game_even_when_files_match():
    settings = WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True, pending_cleanup=True)
    probe = WorkModeProbe(game_path_valid=True, game_running=True, core_available=True,
                          native_load=NativeFeatureProbe(files=True))
    decision = decide_sync_enable(settings, {}, probe)
    assert not decision.ready and decision.action_label == "前往清理游戏目录"


def test_missing_native_component_precedes_paused_mode_guidance():
    settings = WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True,
                                paused=True, pending_cleanup=False)
    probe = WorkModeProbe(game_path_valid=True, core_available=True,
                          native_load=NativeFeatureProbe(files=False))
    missing = decide_sync_enable(settings, {}, probe)
    assert not missing.ready and missing.target == "deployment"
    assert missing.action_label == "前往部署组件"
    deployed = decide_sync_enable(settings, {}, replace(probe, native_load=NativeFeatureProbe(files=True)))
    assert deployed.ready and deployed.action_label is None
    assert "恢复" in deployed.detail


def test_unconfirmed_native_files_wait_without_deploy_guidance():
    settings = WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True,
                                pending_cleanup=False)
    probe = WorkModeProbe(game_path_valid=True, core_available=True,
                          native_load=NativeFeatureProbe(files=None))
    decision = decide_sync_enable(settings, {}, probe)
    assert not decision.ready and decision.action_label is None
