# 验证开启同步的只读决策、进程等待和来源分支。
from dataclasses import replace

from src.domain.work_mode import NativeFeatureProbe, WorkMode, WorkModeProbe, WorkModeSettings
from src.services.sync_enable_preflight import decide_sync_enable


def test_low_mode_requires_npcap_but_not_game_path():
    settings = WorkModeSettings(mode=WorkMode.LOW, risk_confirmed=True)
    probe = WorkModeProbe(core_available=True, npcap_available=False)
    blocked = decide_sync_enable(settings, {}, probe)
    assert not blocked.ready and blocked.target == "npcap"
    assert decide_sync_enable(settings, {}, replace(probe, npcap_available=True)).ready


def test_d3d_waits_for_game_only_when_files_need_work():
    settings = WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True, pending_cleanup=False)
    probe = WorkModeProbe(game_path_valid=True, game_running=True, core_available=True,
                          native_load=NativeFeatureProbe(files=False))
    assert not decide_sync_enable(settings, {}, probe).ready
    assert decide_sync_enable(settings, {}, replace(
        probe, native_load=NativeFeatureProbe(files=True),
    )).ready


def test_loader_waits_for_selected_launcher_and_unknown_process_state():
    settings = WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True, pending_cleanup=False)
    probe = WorkModeProbe(game_path_valid=True, core_available=True,
                          native_load=NativeFeatureProbe(files=False))
    record = {"loading_method": "loader"}
    assert not decide_sync_enable(settings, record, probe).ready
    assert not decide_sync_enable(settings, record, replace(probe, launcher_running=True)).ready
    assert decide_sync_enable(settings, record, replace(probe, launcher_running=False)).ready


def test_pending_cleanup_waits_for_game_even_when_files_match():
    settings = WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True, pending_cleanup=True)
    probe = WorkModeProbe(game_path_valid=True, game_running=True, core_available=True,
                          native_load=NativeFeatureProbe(files=True))
    assert not decide_sync_enable(settings, {}, probe).ready
