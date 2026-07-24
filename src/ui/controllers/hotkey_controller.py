# 从 MainWindow 抽离的控制器方法。
"""Compatibility-installed MainWindow controller."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox
from src.ui.main_window_method_install import install_methods as _install_main_window_methods

_METHOD_NAMES = ["_load_hotkey_config","_save_hotkey_config","_load_update_config","_save_update_config","_load_ui_preferences","_save_ui_preferences","_save_hotkeys"]


def install_methods(app_module, window_cls) -> None:
    _install_main_window_methods(app_module, window_cls, _METHOD_NAMES, globals())


def _load_hotkey_config(self):
    hotkeys=self._account_settings.load("hotkeys")
    self._hk_capture=hotkeys["capture"]; self._hk_finish=hotkeys["finish"]; self._hk_stop=hotkeys["stop"]
def _save_hotkey_config(self):
    self._account_settings.save(
        "hotkeys",
        {
            "capture":self._hk_capture,
            "finish":self._hk_finish,
            "stop":self._hk_stop,
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
    # A QKeySequenceEdit emits an empty intermediate sequence while a user
    # replaces a binding.  Keep the last complete configuration until all
    # fields are valid instead of surfacing an exception to the user.
    if not all((capture, finish, stop)):
        return False
    self._hk_capture=capture
    self._hk_finish=finish
    self._hk_stop=stop
    self._save_hotkey_config()
    if announce:
        QMessageBox.information(self,"保存","快捷键已保存！\n全局截图: "+self._hk_capture+"\n截图完成: "+self._hk_finish+"\n停止: "+self._hk_stop)
    return True

