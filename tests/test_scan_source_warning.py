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


@pytest.mark.parametrize("summary", [None, {"source": "vision"}, {"source": "gamepad"}])
@pytest.mark.parametrize("choice", [QDialog.Accepted, QDialog.Rejected])
def test_unsynced_inventory_cancel_allows_scan_and_go_only_navigates(summary, choice):
    calls, navigation = [], []

    class Dialog:
        def __init__(self, _parent, *, recommend_sync):
            calls.append(recommend_sync)

        def exec(self):
            return choice

    allowed = confirm_scan_mode_entry(
        None, "unused.db", dao_factory=dao_factory(summary), dialog_factory=Dialog,
        navigate_home=lambda: navigation.append("home"),
    )
    assert allowed == (choice == QDialog.Rejected)
    assert calls == [True]
    assert navigation == (["home"] if choice == QDialog.Accepted else [])


def test_sync_recommendation_uses_go_and_cancel_with_sound(monkeypatch):
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
    dialog.close()
    assert dialog.result() == QDialog.Rejected
    dispose(dialog)


@pytest.mark.parametrize("action_text", ["前往", "继续"])
def test_shared_recommendation_plays_once_and_escape_is_cancel(monkeypatch, action_text):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from src.features import input_operation_entry

    app, sounds = application(), []
    monkeypatch.setattr(input_operation_entry.winsound, "MessageBeep", sounds.append)
    dialog = input_operation_entry.OperationRecommendationDialog(
        None, title="操作提示", message="仅提供建议，不自动执行操作。", action_text=action_text,
    )
    dialog.show()
    app.processEvents()
    dialog.hide()
    dialog.show()
    app.processEvents()
    assert sounds == [input_operation_entry.winsound.MB_ICONEXCLAMATION]
    assert dialog.continue_button.text() == action_text
    assert dialog.cancel_button.isDefault()
    QTest.keyClick(dialog, Qt.Key_Escape)
    assert dialog.result() == QDialog.Rejected
    dispose(dialog)


@pytest.mark.parametrize("choice", [QDialog.Accepted, QDialog.Rejected])
def test_confirmation_returns_dialog_choice_for_workbench_snapshot(choice):
    class Dialog:
        def __init__(self, _parent):
            pass

        def exec(self):
            return choice

    allowed = confirm_scan_mode_entry(
        None,
        "unused.db",
        dao_factory=dao_factory({"source": "nte_core"}),
        dialog_factory=Dialog,
        navigate_home=lambda: pytest.fail("同步快照不应跳转工作台"),
    )
    assert allowed == (choice == QDialog.Accepted)


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
def test_declined_scan_entry_restores_previous_mode(monkeypatch, scan_mode):
    from src.features.scanning import workflow

    owner = scan_owner()
    monkeypatch.setattr(
        workflow,
        "_current_scanning_dependencies",
        lambda _owner: type("Dependencies", (), {"user_database_path": "unused.db"})(),
    )
    confirmations = []
    def cancel_warning(_parent, path, **_kwargs):
        confirmations.append(path)
        return False

    monkeypatch.setattr(workflow, "confirm_scan_mode_entry", cancel_warning)

    workflow._on_scan_change(owner, scan_mode, True)

    assert confirmations == ["unused.db"]
    assert owner.scan_group.buttons[4].checked
    assert owner._confirmed_scan_mode_id == 4


@pytest.mark.parametrize("summary", [None, {"source": "vision"}, {"source": "gamepad"}])
@pytest.mark.parametrize("scan_mode", [1, 2, 3])
@pytest.mark.parametrize("choice", [QDialog.Accepted, QDialog.Rejected])
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
        "confirm_scan_mode_entry",
        lambda _parent, _path, **_kwargs: True,
    )

    workflow._on_scan_change(owner, 3, True)

    assert owner._confirmed_scan_mode_id == 3
    assert owner.offline_frame.visible
    assert not owner.total_count_frame.visible
    assert not owner.drone_frame.visible


@pytest.mark.parametrize("scan_mode", [1, 2, 3])
def test_workbench_recommendation_restores_previous_mode_on_go(monkeypatch, scan_mode):
    from src.features.scanning import workflow

    owner, navigation = scan_owner(), []
    owner.navigate = navigation.append
    monkeypatch.setattr(workflow, "_current_scanning_dependencies", lambda _owner:
                        type("Dependencies", (), {"user_database_path": "unused.db"})())

    def recommend(_parent, _path, *, navigate_home):
        navigate_home()
        return False

    monkeypatch.setattr(workflow, "confirm_scan_mode_entry", recommend)
    workflow._on_scan_change(owner, scan_mode, True)
    assert navigation == ["home"]
    assert owner._confirmed_scan_mode_id == 4
    assert owner.scan_group.buttons[4].checked


@pytest.mark.parametrize("choice", [True, False])
def test_medium_scan_management_only_offers_warehouse_navigation(monkeypatch, choice):
    from src.domain.work_mode import WorkMode
    from src.features.scanning import entry_controls

    navigation = []
    owner = type("Owner", (), {"dialog_parent": None})()
    owner.work_mode_provider = lambda: WorkMode.MEDIUM
    owner.navigate = navigation.append
    monkeypatch.setattr(entry_controls, "confirm_operation_recommendation", lambda *_args, **_kwargs: choice)
    monkeypatch.setattr(entry_controls, "_current_scanning_dependencies", lambda _owner:
                        pytest.fail("中风险引导不读取或修改扫描管理配置"))
    entry_controls.open_scan_post_action_manager(owner)
    assert navigation == (["warehouse"] if choice else [])


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
