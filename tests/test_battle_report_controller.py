# 测试战报控制器生命周期。
"""Public lifecycle behavior for the live battle-report overlay."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from src.domain.battle_report import BattleCaptureState, EMPTY_BATTLE_CAPTURE_STATE
from src.features.battle_report.controller import BattleReportController
from src.services.work_mode_service import WorkModeService


class _Overlay:
    def __init__(self) -> None:
        self.hide_count = 0
        self.show_count = 0

    def hide(self) -> None:
        self.hide_count += 1

    def show_overlay(self) -> None:
        self.show_count += 1


class _Page:
    def __init__(self) -> None:
        self.overlay_toggle = SimpleNamespace(isChecked=lambda: True)
        self.states: list[BattleCaptureState] = []

    def update_state(self, state: BattleCaptureState) -> None:
        self.states.append(state)

    def detail_scope(self) -> str:
        return "current"


class _Service:
    def __init__(self) -> None:
        self.stop_requested = False

    def request_stop(self) -> None:
        self.stop_requested = True


class _StartedCaptureService:
    created_kwargs = None

    def __init__(self, **kwargs) -> None:
        type(self).created_kwargs = kwargs
        self.handler = None
        self.started = False

    def add_state_handler(self, handler) -> None:
        self.handler = handler

    def start(self) -> None:
        self.started = True


def _controller_state(*, overlay: _Overlay, page: _Page, service: _Service | None):
    account = SimpleNamespace(active_account_id="test-account")
    return SimpleNamespace(
        _overlay=overlay,
        _page=page,
        _service=service,
        _overlay_capture_active=True,
        _operation_token=7,
        _app_context=SimpleNamespace(account=account, generation=3),
        _frozen_account_id="test-account",
        _frozen_generation=3,
        _latest_state=EMPTY_BATTLE_CAPTURE_STATE,
        _save_detail_scope=lambda _scope: None,
        _restore_inventory_sync=lambda: None,
        _stop_battle_hotkeys=lambda: None,
        _consume_rerecord_terminal=lambda _state: False,
        _manual_stop_requested=False,
        _closing=False,
        _resume_inventory=True,
        is_running=lambda: bool(service is not None),
    )


@pytest.mark.parametrize('persistence_status', ['saved', 'skipped_empty'])
def test_scene_transition_resumes_only_after_old_record_finalization(persistence_status):
    controller = _controller_state(overlay=_Overlay(), page=_Page(), service=_Service())
    calls = []
    controller.start = lambda **kwargs: calls.append(kwargs)
    BattleReportController._apply_state(controller, 7, BattleCaptureState(
        'stopped', '分场完成', False, persistence_status=persistence_status,
        battle_record_id=12 if persistence_status == 'saved' else None,
        end_reason='scene_transition'))
    assert calls == [{'preserve_inventory_pause': True, 'continue_after_scene': True}]
    assert controller._restart_resume_inventory


@pytest.mark.parametrize('reason,manual,phase', [
    ('source_changed', False, 'stopped'), ('scene_transition', True, 'stopped'),
    ('scene_transition', False, 'error'),
])
def test_scene_continuation_respects_manual_stop_and_hard_failure(reason, manual, phase):
    controller = _controller_state(overlay=_Overlay(), page=_Page(), service=_Service())
    calls = []
    controller.start = lambda **kwargs: calls.append(kwargs)
    controller._manual_stop_requested = manual
    BattleReportController._apply_state(controller, 7, BattleCaptureState(
        phase, '终态', False, persistence_status='skipped_empty', end_reason=reason))
    assert calls == []


def test_stop_hides_live_overlay_and_blocks_late_running_state() -> None:
    overlay = _Overlay()
    page = _Page()
    service = _Service()
    controller = _controller_state(overlay=overlay, page=page, service=service)

    BattleReportController.stop(controller)
    BattleReportController._apply_state(
        controller,
        7,
        BattleCaptureState(phase="stopping", message="正在生成战报。", running=True),
    )

    assert service.stop_requested
    assert not controller._overlay_capture_active
    assert overlay.hide_count == 1
    assert overlay.show_count == 0


def test_stopped_capture_hides_overlay_until_the_next_capture_session() -> None:
    overlay = _Overlay()
    page = _Page()
    controller = _controller_state(overlay=overlay, page=page, service=_Service())

    BattleReportController._apply_state(
        controller,
        7,
        BattleCaptureState(phase="stopped", message="战报已生成。", running=False),
    )
    BattleReportController._set_overlay_visible(controller, True)

    assert not controller._overlay_capture_active
    assert overlay.hide_count == 2
    assert overlay.show_count == 0


def test_discarded_capture_restarts_before_inventory_sync_is_restored() -> None:
    overlay = _Overlay()
    page = _Page()
    starts = []
    restores = []
    controller = _controller_state(overlay=overlay, page=page, service=_Service())
    controller._consume_rerecord_terminal = lambda _state: True
    controller.start = lambda **kwargs: starts.append(kwargs)
    controller._restore_inventory_sync = lambda: restores.append(True)

    BattleReportController._apply_state(
        controller,
        7,
        BattleCaptureState(
            phase="stopped",
            message="当前战报已放弃",
            running=False,
            persistence_status="discarded_restart",
        ),
    )

    assert starts == [{"preserve_inventory_pause": True}]
    assert restores == []


@pytest.mark.parametrize("compare", (False, True))
@pytest.mark.parametrize("continuation", (False, True))
def test_start_freezes_raw_capture_setting_and_account_directory(compare: bool, continuation: bool, tmp_path) -> None:
    policy = WorkModeService(tmp_path / "work_mode.json")
    policy.select_mode("developer" if compare else "medium", risk_confirmed=True)
    account = SimpleNamespace(
        active_account_id="test-account",
        active_account_name="测试账号",
        user_database_path=Path("account.sqlite3"),
        log_dir=Path("account-logs"),
    )
    app_context = SimpleNamespace(
        account=account,
        generation=4,
        account_settings=SimpleNamespace(
            load=lambda _key: {
                "capture_device_id": "capture-device",
                "raw_capture_enabled": True,
            }
        ),
        paths=SimpleNamespace(static_database_path=Path("static.sqlite3")),
    )
    page = SimpleNamespace(
        comparison_toggle=SimpleNamespace(isChecked=lambda: compare),
        overlay_toggle=SimpleNamespace(isChecked=lambda: False),
        clear_summary=lambda: None,
        clear_analysis=lambda _message: None,
    )
    overlay = SimpleNamespace(clear_summary=lambda: None, show_overlay=lambda: None)
    controller = SimpleNamespace(
        is_running=lambda: False,
        _work_mode_service=policy,
        _operation_token=0,
        _app_context=app_context,
        _frozen_account_id=None,
        _frozen_generation=None,
        _inventory_sync_is_running=lambda: False,
        _stop_inventory_sync=lambda: None,
        _resume_inventory=False,
        _overlay_capture_active=False,
        _overlay=overlay,
        _page=page,
        _history_factory=lambda _dependencies: object(),
        _history_service=None,
        _history_restored_generation=None,
        _client_factory=lambda data_dir, check: (check(), data_dir)[1],
        _closing=False,
        _comparison_client_factory=lambda data_dir: ("packet", data_dir),
        _apply_comparison=lambda *_args: None,
        _persistence_factory=lambda _dependencies, _operation: object(),
        _state_received=SimpleNamespace(emit=lambda *_args: None),
        _service=None,
        _dialog_parent=None,
        _invalidate_analysis_loading=lambda: None,
        _restart_pending=False,
        _restart_resume_inventory=False,
        _start_battle_hotkeys=lambda: None,
    )

    created = []

    class Capture(_StartedCaptureService):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            created.append(kwargs)

    class Pair(_StartedCaptureService):
        def __init__(self, *, services, parent, defer_packet_start=False):
            self.services = services
            self.started = False
            self.comparison_changed = SimpleNamespace(connect=lambda _callback: None)
            assert defer_packet_start is True

    with (patch("src.features.battle_report.capture_controls.BattleCaptureService", Capture),
          patch("src.features.battle_report.capture_controls.ComparisonCapture", Pair)):
        BattleReportController.start(controller, preserve_inventory_pause=continuation,
                                     continue_after_scene=continuation)

    kwargs = created[0]
    expected_directory = Path("account-logs") / "nte_core" / "raw_capture"
    assert kwargs is not None
    assert kwargs["raw_capture_enabled"] is False
    assert kwargs["raw_capture_directory"] == expected_directory
    assert kwargs["device_name"] == "capture-device"
    assert kwargs["client_factory"]() == expected_directory
    assert controller._service.started
    assert kwargs['required_source'] == 'native'
    assert len(created) == (2 if compare else 1)
    if compare:
        packet = created[1]
        assert kwargs["required_source"] == "native"
        assert packet["required_source"] == "packet"
        assert packet["client_factory"]() == ("packet", expected_directory)
        assert packet["raw_capture_enabled"] is True
        assert packet["operation_context"].operation_id != kwargs["operation_context"].operation_id
        assert packet['comparison_id'] == kwargs['comparison_id'] == kwargs['operation_context'].operation_id


def test_restored_half_scope_loads_its_range_when_no_manual_range_exists() -> None:
    calls = []
    page_transitions = []
    page = SimpleNamespace(
        update_state=lambda _state: None,
        set_detail_scope=lambda _scope: None,
        show_report=lambda: page_transitions.append("report"),
    )
    overlay = SimpleNamespace(update_summary=lambda _summary: None, hide=lambda: None)
    controller = SimpleNamespace(
        _page=page,
        _overlay=overlay,
        _overlay_capture_active=True,
        _operation_token=4,
        _load_analysis=lambda record_id, **kwargs: calls.append((record_id, kwargs)),
    )
    state = BattleCaptureState(
        phase="history",
        message="恢复",
        running=False,
        battle_record_id=12,
    )
    stored = SimpleNamespace(
        battle_record_id=12,
        detail_scope="second",
        analysis_start_us=None,
        analysis_end_us=None,
        analysis_character_id=1004,
        summary=object(),
    )

    BattleReportController._apply_stored_summary(controller, state, stored)
    assert controller._operation_token == 5
    BattleReportController._apply_state(
        controller, 4,
        BattleCaptureState(phase="stopped", message="旧采集通知", running=False),
    )
    assert controller._latest_state is state

    assert calls == [
        (12, {"selected_character_id": 1004, "detail_scope": "second"})
    ]
    assert page_transitions == ["report"]


def test_reset_analysis_range_restores_selected_half_instead_of_whole_battle() -> None:
    calls = []
    controller = SimpleNamespace(
        _latest_state=SimpleNamespace(battle_record_id=12),
        is_running=lambda: False,
        _page=SimpleNamespace(detail_scope=lambda: "first"),
        _load_analysis=lambda record_id, **kwargs: calls.append((record_id, kwargs)),
    )

    BattleReportController._reset_analysis_range(controller)

    assert calls == [(12, {"detail_scope": "first"})]
