# 验证抓包战报保持背包会话连续，原生战报只恢复仍获授权的自动同步。
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.domain.battle_report import BattleCaptureState
from src.features.battle_report.controller import BattleReportController
from src.services.work_mode_service import WorkModeService


@pytest.fixture
def session(tmp_path, monkeypatch):
    policy = WorkModeService(tmp_path / "work_mode.json")
    policy.select_mode("low", risk_confirmed=True)
    inventory = SimpleNamespace(running=True, stops=0, starts=0)
    captures = []

    def stop_inventory():
        inventory.stops += 1
        inventory.running = False

    def start_inventory():
        inventory.starts += 1
        inventory.running = True

    class Capture:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.stopped = False
            captures.append(self)

        def add_state_handler(self, handler):
            self.handler = handler

        def start(self):
            pass

        def request_stop(self):
            self.stopped = True

    account = SimpleNamespace(active_account_id="fixture", log_dir=Path("logs"),
                              user_database_path=Path("fixture.sqlite3"))
    context = SimpleNamespace(account=account, generation=1,
        account_settings=SimpleNamespace(load=lambda _: {}),
        paths=SimpleNamespace(static_database_path=Path("static.sqlite3")))
    owner = SimpleNamespace(is_running=lambda: False, _work_mode_service=policy,
        _operation_token=0, _app_context=context, _closing=False, _dialog_parent=None,
        _invalidate_analysis_loading=lambda: None,
        _inventory_sync_is_running=lambda: inventory.running,
        _stop_inventory_sync=stop_inventory, _start_inventory_sync=start_inventory,
        _history_factory=lambda _: None, _persistence_factory=lambda *_: None,
        _state_received=SimpleNamespace(emit=lambda *_: None),
        _start_battle_hotkeys=lambda: None, _stop_battle_hotkeys=lambda: None,
        _consume_rerecord_terminal=lambda _: False,
        _overlay=SimpleNamespace(clear_summary=lambda: None, hide=lambda: None),
        _page=SimpleNamespace(clear_summary=lambda: None, clear_analysis=lambda _: None,
            update_state=lambda _: None,
            overlay_toggle=SimpleNamespace(isChecked=lambda: False)),
        _client_factory=lambda path, check: object())
    owner._restore_inventory_sync = lambda: BattleReportController._restore_inventory_sync(owner)
    monkeypatch.setattr("src.features.battle_report.capture_controls.BattleCaptureService", Capture)
    monkeypatch.setattr("src.features.battle_report.controller.show_capture_connection_failure", lambda *_: None)
    return owner, policy, inventory, captures


@pytest.mark.parametrize("terminal", ["stopped", "error"])
@pytest.mark.parametrize("continuation", [False, True])
def test_packet_battle_never_stops_or_recreates_existing_inventory(session, terminal, continuation):
    owner, _policy, inventory, captures = session
    owner._restart_resume_inventory = False
    BattleReportController.start(owner, preserve_inventory_pause=continuation)
    assert len(captures) == 1
    assert captures[0].kwargs["required_source"] == "packet"
    assert inventory.running
    assert inventory.stops == 0
    assert not owner._resume_inventory

    BattleReportController._apply_state(owner, owner._operation_token,
        BattleCaptureState(terminal, "fixture", False, persistence_status="skipped_empty"))
    assert inventory.running
    assert inventory.starts == 0
    assert inventory.stops == 0


def test_packet_battle_end_does_not_restart_inventory_user_disabled(session):
    owner, policy, inventory, _captures = session
    BattleReportController.start(owner)
    policy.set_auto_sync_enabled(False)
    owner._stop_inventory_sync()
    BattleReportController._apply_state(owner, owner._operation_token,
        BattleCaptureState("stopped", "fixture", False, persistence_status="skipped_empty"))
    assert not inventory.running
    assert inventory.starts == 0


def test_packet_battle_can_start_with_automatic_inventory_disabled(session):
    owner, policy, inventory, captures = session
    policy.set_auto_sync_enabled(False)
    inventory.running = False
    BattleReportController.start(owner)
    assert len(captures) == 1
    assert captures[0].kwargs["required_source"] == "packet"
    assert not inventory.running
    assert inventory.starts == 0
    assert inventory.stops == 0


def test_packet_battle_manual_stop_only_stops_its_own_service(session):
    owner, _policy, inventory, captures = session
    BattleReportController.start(owner)
    BattleReportController.stop(owner)
    assert captures[0].stopped
    assert inventory.running
    assert inventory.stops == 0


@pytest.mark.parametrize("changed", [
    "none", "paused", "disabled", "account", "generation", "closing", "already_running",
])
def test_native_handoff_restores_only_current_authorized_inventory(session, changed):
    owner, policy, inventory, _captures = session
    policy.select_mode("medium", risk_confirmed=True)
    BattleReportController.start(owner)
    assert inventory.stops == 1
    assert not inventory.running
    assert owner._resume_inventory
    if changed == "paused":
        policy.set_paused(True)
    elif changed == "disabled":
        policy.set_auto_sync_enabled(False)
    elif changed == "account":
        owner._app_context.account.active_account_id = "other"
    elif changed == "generation":
        owner._app_context.generation += 1
    elif changed == "closing":
        owner._closing = True
    elif changed == "already_running":
        inventory.running = True
    owner._restore_inventory_sync()
    assert inventory.starts == (1 if changed == "none" else 0)
    assert not owner._resume_inventory
