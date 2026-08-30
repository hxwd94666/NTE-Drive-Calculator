# 管理战报重录确认、暂存丢弃和采集会话专属全局热键。
"""Battle capture restart controls shared by the report controller."""

from __future__ import annotations

from time import monotonic

from PySide6.QtWidgets import QMessageBox

from src.domain.battle_report import BattleCaptureState
from src.integrations.global_hotkeys import GlobalHotkeyManager


class BattleCaptureControlsMixin:
    """Own the restart intent without moving capture persistence into the UI."""

    _BATTLE_HOTKEY_OWNER = "battle_report"
    _RERECORD_CONFIRM_SECONDS = 1.5

    def _initialize_capture_controls(
        self,
        hotkey_manager: GlobalHotkeyManager,
    ) -> None:
        self._hotkey_manager = hotkey_manager
        self._restart_pending = False
        self._restart_resume_inventory = False
        self._rerecord_hotkey_armed_until = 0.0

    def _start_battle_hotkeys(self) -> None:
        hotkey = self._hotkey_manager.configuration.battle_rerecord
        self._page.set_rerecord_hotkey_label(hotkey)
        self._hotkey_manager.start(
            owner=self._BATTLE_HOTKEY_OWNER,
            on_battle_rerecord=lambda: self._capture_hotkey_received.emit(
                "battle_rerecord"
            ),
        )

    def _stop_battle_hotkeys(self) -> None:
        self._rerecord_hotkey_armed_until = 0.0
        self._hotkey_manager.stop(owner=self._BATTLE_HOTKEY_OWNER)

    def _handle_capture_hotkey(self, action: str) -> None:
        if (
            action != "battle_rerecord"
            or not self.is_running()
            or self._latest_state.phase != "running"
        ):
            return
        now = monotonic()
        if (
            self._rerecord_hotkey_armed_until > 0
            and now <= self._rerecord_hotkey_armed_until
        ):
            self.rerecord(confirm=False)
            return
        self._rerecord_hotkey_armed_until = (
            now + self._RERECORD_CONFIRM_SECONDS
        )
        self._page.show_rerecord_hotkey_confirmation(
            self._hotkey_manager.configuration.battle_rerecord,
            self._RERECORD_CONFIRM_SECONDS,
        )

    def rerecord(self, *, confirm: bool = True) -> None:
        service = self._service
        if (
            service is None
            or not self.is_running()
            or self._latest_state.phase != "running"
            or self._restart_pending
        ):
            return
        if confirm:
            answer = QMessageBox.question(
                self._dialog_parent,
                "放弃当前战报",
                "确定放弃本次尚未保存的战报，并立即重新开始采集吗？",
                (
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                ),
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._restart_pending = True
        self._restart_resume_inventory = self._resume_inventory
        self._rerecord_hotkey_armed_until = 0.0
        self._overlay_capture_active = False
        self._overlay.hide()
        self._stop_battle_hotkeys()
        service.request_discard()

    def _consume_rerecord_terminal(self, state: BattleCaptureState) -> bool:
        restart = (
            self._restart_pending
            and state.phase == "stopped"
            and state.persistence_status == "discarded_restart"
        )
        self._restart_pending = False
        self._rerecord_hotkey_armed_until = 0.0
        if not restart:
            self._restart_resume_inventory = False
        return restart

    def _reset_capture_controls(self) -> None:
        self._stop_battle_hotkeys()
        self._restart_pending = False
        self._restart_resume_inventory = False
