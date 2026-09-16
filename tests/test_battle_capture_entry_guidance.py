# 验证战报实时入口模式引导不会启动或丢弃任务，并区分连接故障与等待。
from types import SimpleNamespace

import pytest

from src.domain.battle_report import BattleCaptureState
from src.features.battle_report.capture_controls import BattleCaptureControlsMixin
from src.features.battle_report.controller import BattleReportController


def blocked_owner():
    class Owner(BattleCaptureControlsMixin):
        pass
    owner = Owner()
    notices = []
    owner.is_running = lambda: False
    owner._work_mode_service = SimpleNamespace(allowed=lambda _cap: False)
    owner._operation_entry = lambda *args: notices.append(args) or False
    owner._service = None
    return owner, notices


def test_offline_start_guides_without_creating_capture():
    owner, notices = blocked_owner()
    owner.start()
    assert notices == [("battle_capture", "战报采集")]
    assert owner._service is None


def test_opening_mode_settings_never_resumes_original_click():
    owner, notices = blocked_owner()
    def navigate(*args):
        notices.append(args)
        owner._work_mode_service.allowed = lambda _cap: True
        return True
    owner._operation_entry = navigate
    owner.start()
    assert owner._service is None and len(notices) == 1


def test_automatic_scene_continuation_does_not_prompt_for_mode():
    owner, notices = blocked_owner()
    owner.start(preserve_inventory_pause=True, continue_after_scene=True)
    assert notices == []


@pytest.mark.parametrize("hotkey", [False, True])
def test_rerecord_button_and_hotkey_check_mode_before_discard(hotkey):
    owner, notices = blocked_owner()
    discarded = []
    owner.is_running = lambda: True
    owner._latest_state = BattleCaptureState("running", "running", True)
    owner._restart_pending = False
    owner._service = SimpleNamespace(request_discard=lambda: discarded.append(True))
    if hotkey:
        owner._handle_capture_hotkey("battle_rerecord")
    else:
        owner.rerecord(confirm=False)
    assert discarded == []
    assert notices == [("battle_capture", "战报采集")]


def state_owner(*, manual=True):
    notices = []
    owner = SimpleNamespace(
        _operation_token=4, _frozen_account_id="account", _frozen_generation=2,
        _app_context=SimpleNamespace(account=SimpleNamespace(active_account_id="account"), generation=2),
        _page=SimpleNamespace(update_state=lambda _state: None, overlay_toggle=SimpleNamespace(isChecked=lambda: False)),
        _overlay=SimpleNamespace(hide=lambda: None), _overlay_capture_active=False,
        _stop_battle_hotkeys=lambda: None, _consume_rerecord_terminal=lambda _state: False,
        _manual_stop_requested=False, _closing=False, _restore_inventory_sync=lambda: None,
        _capture_guidance_enabled=manual, _capture_unavailable_notified=False,
        _capture_guidance_capability="native_battle",
        _work_mode_service=SimpleNamespace(allowed=lambda _cap: True, settings=SimpleNamespace(paused=False)),
        _operation_unavailable=lambda *args, **kwargs: notices.append((args, kwargs)),
    )
    return owner, notices


def test_manual_connection_failure_guides_once_in_current_context():
    owner, notices = state_owner()
    error = BattleCaptureState("error", "capture failed", False, error="missing Core", error_code="NteCoreNotFoundError")
    BattleReportController._apply_state(owner, 4, error)
    BattleReportController._apply_state(owner, 4, error)
    assert notices == [(("战报采集", "missing Core"), {"target": "detection"})]


@pytest.mark.parametrize("kind", ["waiting", "business", "automatic", "stale"])
def test_wait_business_background_and_stale_results_do_not_prompt(kind):
    owner, notices = state_owner(manual=kind != "automatic")
    state = BattleCaptureState(
        "waiting" if kind == "waiting" else "error", "waiting or failure", kind == "waiting",
        error="detail", error_code="SNAPSHOT_SAVE_FAILED" if kind == "business" else "NteCoreNotFoundError",
    )
    if kind == "stale": owner._app_context.generation += 1
    BattleReportController._apply_state(owner, 4, state)
    assert notices == []



@pytest.mark.parametrize("change", ["mode", "pause", "stop", "connected"])
def test_battle_preparation_guidance_is_suppressed_after_revocation_or_readiness(change):
    owner, notices = state_owner()
    if change == "mode": owner._work_mode_service.allowed = lambda _cap: False
    elif change == "pause": owner._work_mode_service.settings.paused = True
    elif change == "stop": owner._manual_stop_requested = True
    else: BattleReportController._apply_state(owner, 4, BattleCaptureState("running", "ready", True))
    BattleReportController._apply_state(owner, 4, BattleCaptureState("error", "failed", False, error_code="NteCoreProcessError"))
    assert notices == []


@pytest.mark.parametrize("automatic", [False, True])
def test_paused_battle_entry_guides_manual_to_detection_without_start(automatic):
    owner, modes = blocked_owner()
    unavailable = []
    owner._work_mode_service = SimpleNamespace(allowed=lambda _cap: True, settings=SimpleNamespace(paused=True))
    owner._operation_unavailable = lambda *args, **kwargs: unavailable.append((args, kwargs))
    owner.start(continue_after_scene=automatic)
    assert owner._service is None and modes == []
    assert len(unavailable) == (0 if automatic else 1)
    if unavailable:
        assert unavailable[0][1] == {"target": "detection"}
