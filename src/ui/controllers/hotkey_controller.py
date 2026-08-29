# 从 MainWindow 抽离的控制器方法。
"""Compatibility-installed MainWindow controller."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox


def _load_hotkey_config(self):
    hotkeys=self._account_settings.load("hotkeys")
    self._hk_capture=hotkeys["capture"]; self._hk_finish=hotkeys["finish"]; self._hk_stop=hotkeys["stop"]
    self._hk_battle_rerecord=hotkeys["battle_rerecord"]
def _save_hotkey_config(self):
    self._account_settings.save(
        "hotkeys",
        {
            "capture":self._hk_capture,
            "finish":self._hk_finish,
            "stop":self._hk_stop,
            "battle_rerecord":self._hk_battle_rerecord,
        },
    )

def _load_update_config(self):
    return self._account_settings.load("update")

def _save_update_config(self):
    self._update_config=self._account_settings.save("update",self._update_config)

def _load_ui_preferences(self):
    return self._account_settings.load("ui")

def _save_ui_preferences(self):
    self._ui_preferences=self._account_settings.save(
        "ui",self._ui_preferences
    )

def _save_hotkeys(self, *, announce=False):
    capture=self._hk_capture_edit.keySequence().toString().strip()
    finish=self._hk_finish_edit.keySequence().toString().strip()
    stop=self._hk_stop_edit.keySequence().toString().strip()
    battle_rerecord=(
        self._hk_battle_rerecord_edit.keySequence().toString().strip()
    )
    # A QKeySequenceEdit emits an empty intermediate sequence while a user
    # replaces a binding.  Keep the last complete configuration until all
    # fields are valid instead of surfacing an exception to the user.
    if not all((capture, finish, stop, battle_rerecord)):
        return False
    self._hk_capture=capture
    self._hk_finish=finish
    self._hk_stop=stop
    self._hk_battle_rerecord=battle_rerecord
    self._save_hotkey_config()
    manager = getattr(self, "global_hotkey_manager", None)
    if manager is not None:
        manager.update_configuration(
            capture_hotkey=capture,
            finish_hotkey=finish,
            stop_hotkey=stop,
            battle_rerecord_hotkey=battle_rerecord,
        )
    if announce:
        QMessageBox.information(
            self,
            "保存",
            "快捷键已保存！\n"
            f"全局截图: {self._hk_capture}\n"
            f"截图完成: {self._hk_finish}\n"
            f"停止: {self._hk_stop}\n"
            f"战报重录: {self._hk_battle_rerecord}",
        )
    return True


class HotkeyControllerMixin:
    _load_hotkey_config = _load_hotkey_config
    _save_hotkey_config = _save_hotkey_config
    _load_update_config = _load_update_config
    _save_update_config = _save_update_config
    _load_ui_preferences = _load_ui_preferences
    _save_ui_preferences = _save_ui_preferences
    _save_hotkeys = _save_hotkeys
