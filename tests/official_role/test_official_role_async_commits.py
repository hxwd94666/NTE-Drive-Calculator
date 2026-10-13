# 验证角色与家具后台保存的冻结输入、失败保留以及离页终态。
from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QMessageBox, QSpinBox, QWidget

from src.features.official_role import role_shell
from src.domain.official_role.models import WorldBonusSettings
from src.ui.controllers.configuration_controller import defer_page_transition


def wait(predicate):
    until = time.monotonic() + 4
    while not predicate() and time.monotonic() < until:
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def role_editor(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = QWidget()
    window.app_context = SimpleNamespace(
        generation=1, account=SimpleNamespace(active_account_id="fixture", user_database_path=tmp_path / "user"),
        paths=SimpleNamespace(config_dir=tmp_path, static_database_path=tmp_path / "static",
                              shared_database_path=tmp_path / "shared", role_catalog=None),
    )
    window.official_role_page_view = QWidget(window)
    window.official_role_world_attack = QDoubleSpinBox(window)
    window.official_role_world_attack.setValue(10)
    window.official_role_world_crit_damage = QDoubleSpinBox(window)
    window.official_role_world_crit_damage.setValue(4)
    awakening = QSpinBox(window)
    awakening.setValue(2)
    fork = QComboBox(window)
    fork.addItem("none", None)
    editor = {"detail": {"profile": {"ordinal": 1}}, "awakening_level": awakening,
              "likeability_level_10": QCheckBox(window), "fork": fork,
              "awakening_checks": {}, "skill_levels": {"Skill-1": 4}}
    window._official_role_editors = {1: editor}
    window._official_role_dirty_ids = {1}
    window._official_role_world_bonus_dirty = True
    window._my_role_dirty = True
    window._official_role_saved_world_bonus = WorldBonusSettings(20, .04)
    mutations, warnings, refreshed = [], [], []
    window.on_configuration_changed = lambda: mutations.append(1)
    controller = role_shell._role_controller(window)
    monkeypatch.setattr(role_shell, "_selected_growth", lambda _editor: (80, 6))
    monkeypatch.setattr(role_shell, "_refresh_my_role", lambda *_args, **_kwargs: refreshed.append(1))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Save)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(controller, "save_profiles", lambda _updates: 1)
    monkeypatch.setattr(controller, "save_world_bonus", lambda settings: settings)
    yield window, controller, mutations, warnings, refreshed
    controller.close()
    wait(lambda: not controller.is_writing() and not controller._reads.is_running())
    window.close()
    window.deleteLater()
    app.processEvents()


def test_profile_and_world_bonus_inputs_are_frozen_before_worker(role_editor, monkeypatch):
    window, controller, mutations, _warnings, refreshed = role_editor
    release, saved = threading.Event(), []
    monkeypatch.setattr(controller, "save_profiles", lambda updates: (release.wait(2), saved.append(updates)))
    worlds = []
    monkeypatch.setattr(controller, "save_world_bonus", worlds.append)
    assert role_shell._save_profiles(window)
    assert window._my_role_dirty and not window.official_role_page_view.isEnabled()
    assert not role_shell._save_profiles(window)
    window._official_role_editors[1]["skill_levels"]["Skill-1"] = 9
    window.official_role_world_attack.setValue(99)
    release.set()
    wait(lambda: not controller.is_writing())
    assert saved[0][0].skill_levels == {"Skill-1": 4}
    assert worlds == [WorldBonusSettings(10, .04)]
    assert mutations == [1] and refreshed == [1]
    assert not window._my_role_dirty and window.official_role_page_view.isEnabled()


def test_partial_service_failure_retains_whole_draft_for_review(role_editor, monkeypatch):
    window, controller, mutations, warnings, refreshed = role_editor
    saved = []
    monkeypatch.setattr(controller, "save_profiles", lambda _updates: saved.append(1))
    def failed(_settings):
        raise RuntimeError("world save failed")
    monkeypatch.setattr(controller, "save_world_bonus", failed)
    destinations = []
    defer_page_transition(window, lambda: destinations.append(1), pages=("my_role",))
    wait(lambda: not controller.is_writing())
    assert saved == [1] and warnings
    assert window._my_role_dirty and window._official_role_dirty_ids == {1}
    assert not destinations and not mutations and not refreshed


def test_role_leave_save_only_continues_once(role_editor, monkeypatch):
    window, controller, _mutations, _warnings, _refreshed = role_editor
    release = threading.Event()
    monkeypatch.setattr(controller, "save_profiles", lambda _updates: release.wait(2))
    destinations = []
    defer_page_transition(window, lambda: destinations.append(1), pages=("my_role",))
    defer_page_transition(window, lambda: destinations.append(2), pages=("my_role",))
    assert not destinations
    release.set()
    wait(lambda: bool(destinations))
    assert destinations == [1]


def test_stale_generation_does_not_clear_new_account_or_navigate(role_editor, monkeypatch):
    window, controller, mutations, _warnings, _refreshed = role_editor
    release = threading.Event()
    monkeypatch.setattr(controller, "save_profiles", lambda _updates: release.wait(2))
    destinations = []
    defer_page_transition(window, lambda: destinations.append(1), pages=("my_role",))
    window.app_context.generation += 1
    release.set()
    wait(lambda: not controller.is_writing())
    assert not destinations and not mutations and window._my_role_dirty
    assert window._pending_page_transition is None


def test_discard_restores_cached_furniture_without_main_thread_sql(role_editor, monkeypatch):
    window, controller, _mutations, _warnings, _refreshed = role_editor
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Discard)
    monkeypatch.setattr(controller, "load_world_bonus", lambda: pytest.fail("discard performed synchronous SQL"))
    assert role_shell.confirm_pending_my_role_changes(window)
    assert window.official_role_world_attack.value() == 20
    assert not window._my_role_dirty and window._official_role_source_key is None
