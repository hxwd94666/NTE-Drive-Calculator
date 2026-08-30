# 验证战报放弃重录按钮与双击热键确认边界。
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox

from src.domain.battle_report import BattleCaptureState
from src.features.battle_report.capture_controls import (
    BattleCaptureControlsMixin,
)


class _Service:
    def __init__(self) -> None:
        self.discard_requested = False

    def request_discard(self) -> None:
        self.discard_requested = True


class _Owner(BattleCaptureControlsMixin):
    pass


def _owner() -> _Owner:
    owner = _Owner()
    owner._service = _Service()
    owner._page = SimpleNamespace(
        show_rerecord_hotkey_confirmation=lambda _hotkey, _seconds: None,
    )
    owner._dialog_parent = None
    owner._overlay = SimpleNamespace(hide=lambda: None)
    owner._overlay_capture_active = True
    owner._resume_inventory = True
    owner._restart_pending = False
    owner._restart_resume_inventory = False
    owner._rerecord_hotkey_armed_until = 0.0
    owner._latest_state = BattleCaptureState(
        phase="running",
        message="采集中",
        running=True,
    )
    owner._hotkey_manager = SimpleNamespace(
        configuration=SimpleNamespace(battle_rerecord="F11"),
        stop=lambda **_kwargs: None,
    )
    owner.is_running = lambda: True
    return owner


def test_double_hotkey_requires_second_press_inside_confirmation_window() -> None:
    owner = _owner()
    notices = []
    owner._page.show_rerecord_hotkey_confirmation = (
        lambda hotkey, seconds: notices.append((hotkey, seconds))
    )

    with patch(
        "src.features.battle_report.capture_controls.monotonic",
        side_effect=(10.0, 10.8),
    ):
        BattleCaptureControlsMixin._handle_capture_hotkey(
            owner,
            "battle_rerecord",
        )
        assert not owner._service.discard_requested
        BattleCaptureControlsMixin._handle_capture_hotkey(
            owner,
            "battle_rerecord",
        )

    assert notices == [("F11", 1.5)]
    assert owner._service.discard_requested
    assert owner._restart_pending


def test_button_rerecord_requires_explicit_confirmation() -> None:
    owner = _owner()

    with patch.object(
        QMessageBox,
        "question",
        return_value=QMessageBox.StandardButton.No,
    ):
        BattleCaptureControlsMixin.rerecord(owner)
    assert not owner._service.discard_requested

    with patch.object(
        QMessageBox,
        "question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        BattleCaptureControlsMixin.rerecord(owner)
    assert owner._service.discard_requested


def test_only_discard_success_consumes_pending_immediate_restart() -> None:
    owner = _owner()
    owner._restart_pending = True

    assert BattleCaptureControlsMixin._consume_rerecord_terminal(
        owner,
        BattleCaptureState(
            phase="stopped",
            message="已丢弃",
            running=False,
            persistence_status="discarded_restart",
        ),
    )
    assert not owner._restart_pending
