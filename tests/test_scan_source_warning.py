# 验证工作台同步快照会在切换到三种扫描模式时立即触发继续确认。
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QDialog

from auto_sync_ui_fixture import application, dispose
from src.features.scanning import scan_source_warning as warning_module
from src.features.scanning.scan_source_warning import (
    SCAN_SOURCE_WARNING,
    ScanSourceWarningDialog,
    confirm_scan_mode_after_workbench_sync,
    current_snapshot_is_workbench_sync,
    restore_scan_mode_selection,
)


class FakeDao:
    def __init__(self, _database_path, summary):
        self.summary = summary

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def current_inventory_summary(self):
        return self.summary


def dao_factory(summary):
    return lambda database_path: FakeDao(database_path, summary)


def test_only_current_nte_core_snapshot_is_treated_as_workbench_sync():
    assert current_snapshot_is_workbench_sync("unused.db", dao_factory=dao_factory({"source": "nte_core"}))
    assert not current_snapshot_is_workbench_sync("unused.db", dao_factory=dao_factory({"source": "vision"}))
    assert not current_snapshot_is_workbench_sync("unused.db", dao_factory=dao_factory(None))


def test_warning_text_and_bottom_right_button_order():
    app = application()
    dialog = ScanSourceWarningDialog()
    assert dialog.message.text() == SCAN_SOURCE_WARNING
    assert dialog.buttons.itemAt(0).spacerItem() is not None
    assert dialog.buttons.itemAt(1).widget() is dialog.continue_button
    assert dialog.buttons.itemAt(2).widget() is dialog.cancel_button
    assert dialog.cancel_button.isDefault()
    dialog.continue_button.click()
    assert dialog.result() == QDialog.Accepted
    app.processEvents()
    dispose(dialog)


def test_warning_sound_plays_once_when_dialog_is_shown(monkeypatch):
    app = application()
    sounds = []
    monkeypatch.setattr(warning_module, "play_warning_sound", lambda: sounds.append("warning"))
    dialog = ScanSourceWarningDialog()

    dialog.show()
    app.processEvents()
    dialog.hide()
    dialog.show()
    app.processEvents()

    assert sounds == ["warning"]
    dispose(dialog)


def test_warning_sound_uses_windows_exclamation_sound(monkeypatch):
    sounds = []
    monkeypatch.setattr(warning_module.winsound, "MessageBeep", sounds.append)
    warning_module.play_warning_sound()
    assert sounds == [warning_module.winsound.MB_ICONEXCLAMATION]


def test_confirmation_is_skipped_for_visual_snapshot():
    calls = []

    def dialog_factory(_parent):
        calls.append(True)
        raise AssertionError("视觉快照不应显示工作台同步警告")

    assert confirm_scan_mode_after_workbench_sync(
        None,
        "unused.db",
        dao_factory=dao_factory({"source": "vision"}),
        dialog_factory=dialog_factory,
    )
    assert not calls


def test_confirmation_returns_dialog_choice_for_workbench_snapshot():
    class Dialog:
        def __init__(self, _parent):
            pass

        def exec(self):
            return QDialog.Rejected

    assert not confirm_scan_mode_after_workbench_sync(
        None,
        "unused.db",
        dao_factory=dao_factory({"source": "nte_core"}),
        dialog_factory=Dialog,
    )


class VisibilityFrame:
    def __init__(self):
        self.visible = None

    def setVisible(self, visible):
        self.visible = visible


class ScanButton:
    def __init__(self):
        self.checked = False

    def setChecked(self, checked):
        self.checked = checked


class ScanGroup:
    def __init__(self):
        self.blocked = False
        self.buttons = {mode: ScanButton() for mode in (1, 2, 3, 4)}

    def blockSignals(self, blocked):
        previous = self.blocked
        self.blocked = blocked
        return previous

    def button(self, button_id):
        return self.buttons.get(button_id)


def scan_owner():
    owner = type("Owner", (), {})()
    owner.scan_group = ScanGroup()
    owner.dialog_parent = None
    owner._confirmed_scan_mode_id = 4
    owner.offline_frame = VisibilityFrame()
    owner.total_count_frame = VisibilityFrame()
    owner.full_scan_driver_frame = VisibilityFrame()
    owner.scan_dual_thread_frame = VisibilityFrame()
    owner.drone_frame = VisibilityFrame()
    return owner


def test_restore_scan_mode_blocks_recursive_toggle_signal():
    group = ScanGroup()
    restore_scan_mode_selection(group, 4)
    assert group.buttons[4].checked
    assert not group.blocked


@pytest.mark.parametrize("scan_mode", [1, 2, 3])
def test_three_scan_modes_warn_immediately_and_cancel_restores_previous_mode(monkeypatch, scan_mode):
    from src.features.scanning import workflow

    owner = scan_owner()
    monkeypatch.setattr(
        workflow,
        "_current_scanning_dependencies",
        lambda _owner: type("Dependencies", (), {"user_database_path": "unused.db"})(),
    )
    confirmations = []
    def cancel_warning(_parent, path):
        confirmations.append(path)
        return False

    monkeypatch.setattr(workflow, "confirm_scan_mode_after_workbench_sync", cancel_warning)

    workflow._on_scan_change(owner, scan_mode, True)

    assert confirmations == ["unused.db"]
    assert owner.scan_group.buttons[4].checked
    assert owner._confirmed_scan_mode_id == 4


def test_unchecked_signal_and_direct_inventory_mode_do_not_warn(monkeypatch):
    from src.features.scanning import workflow

    owner = scan_owner()
    monkeypatch.setattr(
        workflow,
        "confirm_scan_mode_after_workbench_sync",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不应显示警告")),
    )
    workflow._on_scan_change(owner, 3, False)
    workflow._on_scan_change(owner, 4, True)
    assert owner._confirmed_scan_mode_id == 4


def test_continue_commits_selected_mode_and_updates_visible_options(monkeypatch):
    from src.features.scanning import workflow

    owner = scan_owner()
    monkeypatch.setattr(
        workflow,
        "_current_scanning_dependencies",
        lambda _owner: type("Dependencies", (), {"user_database_path": "unused.db"})(),
    )
    monkeypatch.setattr(
        workflow,
        "confirm_scan_mode_after_workbench_sync",
        lambda _parent, _path: True,
    )

    workflow._on_scan_change(owner, 3, True)

    assert owner._confirmed_scan_mode_id == 3
    assert owner.offline_frame.visible
    assert not owner.total_count_frame.visible
    assert not owner.drone_frame.visible
