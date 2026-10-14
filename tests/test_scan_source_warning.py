# 验证三种扫描入口可取消同步建议并继续选择模式，前往仅导航工作台。
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QDialog

from auto_sync_ui_fixture import application, dispose
from src.features.scanning import scan_source_warning as warning_module
from src.features.scanning.scan_source_warning import (
    SCAN_SOURCE_WARNING,
    ScanSourceWarningDialog,
    SCAN_SYNC_RECOMMENDATION,
    confirm_scan_mode_entry,
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


def test_warning_defaults_to_cancel_and_explicit_continue_accepts():
    app = application()
    dialog = ScanSourceWarningDialog()
    assert dialog.message.text() == SCAN_SOURCE_WARNING
    assert dialog.cancel_button.isDefault()
    dialog.continue_button.click()
    assert dialog.result() == QDialog.Accepted
    app.processEvents()
    dispose(dialog)


def test_warning_plays_exclamation_sound_once_when_shown(monkeypatch):
    app = application()
    sounds = []
    monkeypatch.setattr(warning_module.winsound, "MessageBeep", sounds.append)
    dialog = ScanSourceWarningDialog()

    dialog.show()
    app.processEvents()
    dialog.hide()
    dialog.show()
    app.processEvents()

    assert sounds == [warning_module.winsound.MB_ICONEXCLAMATION]
    dispose(dialog)


def test_sync_recommendation_defaults_to_cancel_and_escape_rejects(monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    app, sounds = application(), []
    monkeypatch.setattr(warning_module, "play_warning_sound", lambda: sounds.append(True))
    dialog = ScanSourceWarningDialog(recommend_sync=True)
    assert dialog.message.text() == SCAN_SYNC_RECOMMENDATION
    assert dialog.continue_button.text() == "前往"
    assert dialog.cancel_button.text() == "取消"
    assert dialog.cancel_button.isDefault()
    dialog.show()
    app.processEvents()
    assert sounds == [True]
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert dialog.result() == QDialog.Rejected
    dispose(dialog)


@pytest.mark.parametrize("choice", [QDialog.Accepted, QDialog.Rejected])
def test_workbench_snapshot_requires_confirmation_and_rejection_restores_mode(monkeypatch, choice):
    from src.features.scanning import workflow

    owner = scan_owner()
    owner.scan_group.button(1).setChecked(True)
    monkeypatch.setattr(
        workflow, "_current_scanning_dependencies",
        lambda _owner: type("Dependencies", (), {"user_database_path": "unused.db"})(),
    )

    class Dialog:
        def __init__(self, _parent):
            pass

        def exec(self):
            return choice

    def confirm_entry(parent, path, *, navigate_home):
        return confirm_scan_mode_entry(
            parent, path, navigate_home=navigate_home,
            dao_factory=dao_factory({"source": "nte_core"}), dialog_factory=Dialog,
        )

    owner.navigate = lambda _key: pytest.fail("同步快照不应跳转工作台")
    monkeypatch.setattr(workflow, "confirm_scan_mode_entry", confirm_entry)
    workflow._on_scan_change(owner, 1, True)

    if choice == QDialog.Accepted:
        assert owner._confirmed_scan_mode_id == 1
        assert owner.total_count_frame.visible
        assert owner.full_scan_driver_frame.visible
        assert owner.scan_dual_thread_frame.visible
        assert not owner.offline_frame.visible
        assert not owner.drone_frame.visible
        assert owner.scan_group.signal_blocks == []
    else:
        assert owner._confirmed_scan_mode_id == 4
        assert owner.scan_group.button(4).checked
        assert all(frame.visible is None for frame in (
            owner.offline_frame, owner.total_count_frame, owner.full_scan_driver_frame,
            owner.scan_dual_thread_frame, owner.drone_frame,
        ))
        assert owner.scan_group.signal_blocks == [True, False]
        assert not owner.scan_group.blocked


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
        self.signal_blocks = []
        self.buttons = {mode: ScanButton() for mode in (1, 2, 3, 4)}

    def blockSignals(self, blocked):
        previous = self.blocked
        self.blocked = blocked
        self.signal_blocks.append(blocked)
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


# 来源区分提示分支；三种模式各保留前往回退与拒绝建议后进入，不穷举互不影响的交叉组合。
@pytest.mark.parametrize("summary,scan_mode,choice", [
    (None, 1, QDialog.Accepted), (None, 1, QDialog.Rejected),
    ({"source": "vision"}, 2, QDialog.Accepted), ({"source": "vision"}, 2, QDialog.Rejected),
    ({"source": "gamepad"}, 3, QDialog.Accepted), ({"source": "gamepad"}, 3, QDialog.Rejected),
])
def test_unsynced_scan_mode_entry_preserves_choice_or_only_navigates(
    monkeypatch, summary, scan_mode, choice,
):
    from src.features.scanning import workflow

    owner, navigation, recommendations = scan_owner(), [], []
    owner.navigate = navigation.append
    owner.scan_group.button(scan_mode).setChecked(True)
    monkeypatch.setattr(
        workflow, "_current_scanning_dependencies",
        lambda _owner: type("Dependencies", (), {"user_database_path": "unused.db"})(),
    )

    class Dialog:
        def __init__(self, _parent, *, recommend_sync):
            recommendations.append(recommend_sync)

        def exec(self):
            return choice

    def confirm_entry(parent, path, *, navigate_home):
        return confirm_scan_mode_entry(
            parent, path, navigate_home=navigate_home,
            dao_factory=dao_factory(summary), dialog_factory=Dialog,
        )

    monkeypatch.setattr(workflow, "confirm_scan_mode_entry", confirm_entry)
    workflow._on_scan_change(owner, scan_mode, True)

    assert recommendations == [True]
    if choice == QDialog.Accepted:
        assert navigation == ["home"]
        assert owner._confirmed_scan_mode_id == 4
        assert owner.scan_group.button(4).checked
        assert owner.offline_frame.visible is None
        assert owner.total_count_frame.visible is None
        assert owner.drone_frame.visible is None
    else:
        assert navigation == []
        assert owner._confirmed_scan_mode_id == scan_mode
        assert owner.scan_group.button(scan_mode).checked
        assert not owner.scan_group.button(4).checked
        assert owner.offline_frame.visible == (scan_mode == 3)
        assert owner.total_count_frame.visible == (scan_mode == 1)
        assert owner.full_scan_driver_frame.visible == (scan_mode == 1)
        assert owner.scan_dual_thread_frame.visible == (scan_mode == 1)
        assert owner.drone_frame.visible == (scan_mode == 2)


def test_unchecked_signal_and_direct_inventory_mode_do_not_warn(monkeypatch):
    from src.features.scanning import workflow

    owner = scan_owner()
    monkeypatch.setattr(
        workflow,
        "confirm_scan_mode_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应显示警告")),
    )
    workflow._on_scan_change(owner, 3, False)
    workflow._on_scan_change(owner, 4, True)
    assert owner._confirmed_scan_mode_id == 4


@pytest.mark.parametrize("choice", [True, False])
def test_medium_scan_management_cancel_opens_manager_and_forward_only_navigates(monkeypatch, choice):
    from src.domain.work_mode import WorkMode
    from src.features.scanning import entry_controls

    navigation, managers = [], []
    owner = type("Owner", (), {"dialog_parent": None})()
    owner.work_mode_provider = lambda: WorkMode.MEDIUM
    owner.navigate = navigation.append
    monkeypatch.setattr(entry_controls, "confirm_operation_recommendation", lambda *_args, **_kwargs: choice)
    dependencies = type("Dependencies", (), dict.fromkeys(
        ("user_config_dir", "config_dir", "user_database_path", "static_database_path", "game_ui_asset_root"), "unused"))()
    monkeypatch.setattr(entry_controls, "_current_scanning_dependencies", lambda _owner: dependencies)
    monkeypatch.setattr(entry_controls, "show_scan_post_action_dialog", lambda *_args, **_kwargs: managers.append(True))
    entry_controls.open_scan_post_action_manager(owner)
    assert navigation == (["warehouse"] if choice else [])
    assert managers == ([] if choice else [True])


def test_non_medium_scan_management_retains_existing_dialog(monkeypatch):
    from src.domain.work_mode import WorkMode
    from src.features.scanning import entry_controls

    owner = type("Owner", (), {"dialog_parent": None})()
    owner.work_mode_provider = lambda: WorkMode.LOW
    dependencies = type("Dependencies", (), dict.fromkeys(
        ("user_config_dir", "config_dir", "user_database_path", "static_database_path", "game_ui_asset_root"), "unused"))()
    calls = []
    monkeypatch.setattr(entry_controls, "_current_scanning_dependencies", lambda _owner: dependencies)
    monkeypatch.setattr(entry_controls, "confirm_operation_recommendation", lambda *_args, **_kwargs:
                        pytest.fail("低风险不显示中风险建议"))
    monkeypatch.setattr(entry_controls, "show_scan_post_action_dialog", lambda *_args, **_kwargs: calls.append(True))
    entry_controls.open_scan_post_action_manager(owner)
    assert calls == [True]
