# 验证战报启动检查从控制器贯通到会话，并将用户取消与真实启动故障分开处理。
from concurrent.futures import CancelledError
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from src.features.battle_report.capture_controls import BattleCaptureControlsMixin
from src.features.battle_report.dependencies import build_battle_report_controller
from src.observability import OperationContext
from src.services.battle_capture_service import BattleCaptureService
from tests.test_battle_capture_axis_service import _Writer, _wait_until


@pytest.mark.parametrize("change", ["stop", "close", "rerecord", "token", "account", "generation"])
def test_capture_factory_checks_frozen_start_before_and_during_handoff(monkeypatch, change):
    created, checks = [], []

    class Capture:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def add_state_handler(self, _handler):
            pass

        def start(self):
            pass

    account = SimpleNamespace(active_account_id="fixture", log_dir=Path("logs"),
                              user_database_path=Path("fixture.sqlite3"))
    context = SimpleNamespace(account=account, generation=1,
        account_settings=SimpleNamespace(load=lambda _: {}),
        paths=SimpleNamespace(static_database_path=Path("static.sqlite3")))
    policy = SimpleNamespace(allowed=lambda cap: cap == "native_battle", require=lambda _: None)
    owner = SimpleNamespace(is_running=lambda: False, _work_mode_service=policy,
        _operation_token=0, _app_context=context, _closing=False,
        _invalidate_analysis_loading=lambda: None, _inventory_sync_is_running=lambda: False,
        _history_factory=lambda _: None, _persistence_factory=lambda *_: None,
        _state_received=SimpleNamespace(emit=lambda *_: None), _start_battle_hotkeys=lambda: None,
        _overlay=SimpleNamespace(clear_summary=lambda: None),
        _page=SimpleNamespace(clear_summary=lambda: None, clear_analysis=lambda _: None,
                              overlay_toggle=SimpleNamespace(isChecked=lambda: False)),
        _client_factory=lambda path, check: checks.append(check) or path)
    monkeypatch.setattr("src.features.battle_report.capture_controls.BattleCaptureService", Capture)
    BattleCaptureControlsMixin.start(owner)
    create = created[0]["client_factory"]
    assert create() == Path("logs/nte_core/raw_capture")
    if change == "stop":
        owner._manual_stop_requested = True
    elif change == "close":
        owner._closing = True
    elif change == "rerecord":
        owner._restart_pending = True
    elif change == "token":
        owner._operation_token += 1
    elif change == "account":
        account.active_account_id = "other"
    else:
        context.generation += 1
    with pytest.raises(CancelledError):
        checks[0]()
    with pytest.raises(CancelledError):
        create()
    assert len(checks) == 1


def test_composition_forwards_cancellation_check_to_native_session(monkeypatch):
    calls = []
    monkeypatch.setattr("src.features.battle_report.dependencies.BattleReportController", lambda **kwargs: kwargs)
    policy = SimpleNamespace(allowed=lambda cap: cap == "native_battle")
    session = SimpleNamespace(battle_client=lambda **kwargs: calls.append(kwargs) or "lease")
    composed = build_battle_report_controller(app_context=SimpleNamespace(), dialog_parent=None,
        inventory_sync_is_running=lambda: False, stop_inventory_sync=lambda: None,
        start_inventory_sync=lambda: None, hotkey_manager=None, work_mode_service=policy,
        native_session=session)
    check = lambda: None
    assert composed["client_factory"](Path("logs"), check) == "lease"
    assert calls == [{"check": check}]


@pytest.mark.parametrize("requested,error,phase", [
    (True, CancelledError("cancelled"), "stopped"),
    (False, CancelledError("unexpected cancellation"), "error"),
    (True, RuntimeError("connection failed"), "error"),
])
def test_only_requested_factory_cancellation_is_normal_empty_capture(requested, error, phase):
    entered, release = Event(), Event()
    writer = _Writer()

    def factory():
        entered.set()
        assert release.wait(2)
        raise error

    service = BattleCaptureService(client_factory=factory, operation_guard=lambda _: None,
        operation_context=OperationContext.create("battle_report"), summary_writer=writer,
        required_source="native")
    service.start()
    try:
        assert entered.wait(1)
        if requested:
            service.request_stop()
        release.set()
        _wait_until(lambda: not service.is_running)
        assert service.state.phase == phase
        assert writer.discarded
        if phase == "stopped":
            assert service.state.persistence_status == "skipped_empty"
        else:
            assert service.state.error == str(error)
    finally:
        release.set()
        service.close()
