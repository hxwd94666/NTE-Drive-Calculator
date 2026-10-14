# 验证权重异步读写的草稿、账号隔离和离页保存只跳转一次。
from __future__ import annotations

import os
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

NTE_TEST_TIER = "core"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QVBoxLayout, QWidget

from src.features.configuration.editing import form, commit
from src.features.configuration.page_owner import BasicWeightPage
from src.ui.controllers.configuration_controller import defer_page_transition


def wait(predicate):
    until = time.monotonic() + 4
    while not predicate() and time.monotonic() < until:
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def editor(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    shell = QWidget()
    context = SimpleNamespace(
        generation=1, account=SimpleNamespace(active_account_id="fixture", user_database_path=tmp_path / "user"),
        paths=SimpleNamespace(config_dir=Path("config"), equipment_allocation_database_path=tmp_path / "static",
                              shared_database_path=tmp_path / "shared"),
    )
    mutations, rendered, warnings = [], [], []
    window = BasicWeightPage(context, lambda _key: None, lambda: mutations.append(1), shell)
    shell.app_context = context
    shell.basic_weight_page = window
    window.shell = shell
    QVBoxLayout(shell).addWidget(window)
    controller = window.controller()
    model = {"roles": {"Fixture": {"character_id": 1, "weights": {"CritBase": 1.0}}},
             **{key: [] for key in ("property_labels", "sub_choices", "main_choices", "shape_bonus_choices", "shape_label_choices", "suit_choices")}}
    monkeypatch.setattr(controller, "load_form_data", lambda: deepcopy(model))

    def render(_window, roles, active_role=None):
        rendered.append(deepcopy(roles))
        window.config_form_layout.addWidget(QLabel("fixture form"))
    monkeypatch.setattr(form, "render_roles_form", render)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Save)
    form.switch_config_form(window)
    wait(lambda: bool(rendered))
    yield window, controller, model, mutations, rendered, warnings
    controller.close()
    wait(lambda: not controller._reads.is_running() and not controller.is_writing())
    shell.close()
    shell.deleteLater()
    app.processEvents()


def dirty(window):
    window._config_dirty = True
    window._config_dirty_character_ids = {1}
    window._config_form_data["Fixture"]["weights"]["CritBase"] = 2.0


def test_unchanged_read_reuses_form_and_read_never_overwrites_draft(editor):
    window, controller, _model, _mutations, rendered, _warnings = editor
    form.switch_config_form(window)
    wait(lambda: not controller._reads.is_running())
    assert len(rendered) == 1
    dirty(window)
    form.switch_config_form(window)
    assert window._config_form_data["Fixture"]["weights"]["CritBase"] == 2
    assert len(rendered) == 1


def test_save_freezes_input_and_does_not_block_or_write_twice(editor, monkeypatch):
    window, controller, _model, mutations, _rendered, _warnings = editor
    release, entered = threading.Event(), threading.Event()
    saved = []

    def save(data, *_fields):
        entered.set()
        release.wait(2)
        saved.append(data["Fixture"]["weights"]["CritBase"])

    monkeypatch.setattr(controller, "save_changes", save)
    dirty(window)
    assert commit.save_config_form(window)
    wait(entered.is_set)
    assert window._config_dirty and not window.isEnabled()
    assert not commit.save_config_form(window)
    window._config_form_data["Fixture"]["weights"]["CritBase"] = 99  # Simulate a caller mutating the original object.
    release.set()
    wait(lambda: not controller.is_writing())
    assert saved == [2] and mutations == [1]
    assert not window._config_dirty and window.isEnabled()


def test_leave_save_waits_for_commit_and_navigates_once(editor, monkeypatch):
    window, controller, _model, _mutations, _rendered, _warnings = editor
    release = threading.Event()
    monkeypatch.setattr(controller, "save_changes", lambda *_args: release.wait(2))
    dirty(window)
    destinations = []
    assert defer_page_transition(window.shell, lambda: destinations.append("first"), pages=("config",))
    assert defer_page_transition(window.shell, lambda: destinations.append("second"), pages=("config",))
    assert destinations == []
    release.set()
    wait(lambda: bool(destinations))
    assert destinations == ["first"]


def test_failed_save_stays_in_editor_with_draft_and_no_navigation(editor, monkeypatch):
    window, controller, _model, mutations, _rendered, warnings = editor
    def fail(*_args):
        raise RuntimeError("fixture failed")
    monkeypatch.setattr(controller, "save_changes", fail)
    dirty(window)
    destinations = []
    defer_page_transition(window.shell, lambda: destinations.append(1), pages=("config",))
    wait(lambda: not controller.is_writing())
    assert warnings and window._config_dirty
    assert window._config_form_data["Fixture"]["weights"]["CritBase"] == 2
    assert destinations == [] and mutations == []
    assert window.shell._pending_page_transition is None


def test_transition_during_explicit_save_waits_without_second_commit(editor, monkeypatch):
    window, controller, _model, _mutations, _rendered, _warnings = editor
    release, calls = threading.Event(), []
    monkeypatch.setattr(controller, "save_changes", lambda *_args: (calls.append(1), release.wait(2)))
    dirty(window)
    commit.save_config_form(window)
    destinations = []
    defer_page_transition(window.shell, lambda: destinations.append(1), pages=("config",))
    release.set()
    wait(lambda: bool(destinations))
    assert calls == [1] and destinations == [1]


def test_account_generation_change_drops_old_ack_and_pending_navigation(editor, monkeypatch):
    window, controller, _model, mutations, _rendered, _warnings = editor
    release = threading.Event()
    monkeypatch.setattr(controller, "save_changes", lambda *_args: release.wait(2))
    dirty(window)
    destinations = []
    defer_page_transition(window.shell, lambda: destinations.append(1), pages=("config",))
    window.app_context.generation += 1
    release.set()
    wait(lambda: not controller.is_writing())
    assert destinations == [] and mutations == []
    assert window._config_dirty and window.shell._pending_page_transition is None


def test_cancel_and_discard_keep_existing_navigation_rules(editor, monkeypatch):
    window, controller, _model, _mutations, rendered, _warnings = editor
    dirty(window)
    destinations = []
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Cancel)
    defer_page_transition(window.shell, lambda: destinations.append(1), pages=("config",))
    assert window._config_dirty and not destinations
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Discard)
    defer_page_transition(window.shell, lambda: destinations.append(1), pages=("config",))
    wait(lambda: bool(destinations))
    assert not window._config_dirty and destinations == [1]
    form.switch_config_form(window)
    wait(lambda: not controller._reads.is_running())
    assert window._config_form_data["Fixture"]["weights"]["CritBase"] == 1
    assert len(rendered) == 2
