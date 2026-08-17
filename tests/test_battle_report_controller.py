# 测试战报控制器生命周期。
"""Public lifecycle behavior for the live battle-report overlay."""

from types import SimpleNamespace

from src.domain.battle_report import BattleCaptureState, EMPTY_BATTLE_CAPTURE_STATE
from src.features.battle_report.controller import BattleReportController


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
        is_running=lambda: bool(service is not None),
    )


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
