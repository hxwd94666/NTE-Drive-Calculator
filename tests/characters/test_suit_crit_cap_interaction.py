# 验证角色偏好中的套装、效果模式和弧盘共用暴击上限联动，并保留取消与手填时序。
import os
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLineEdit

from src.features.allocation.selection.role_selector import RoleSelector
from src.solver.set_effects import FOUR_PIECE, TWO_PIECE
from src.ui.common.widgets import SearchableComboBox


@pytest.fixture
def selector(tmp_path):
    app = QApplication.instance() or QApplication([])
    owner = RoleSelector(priority_config_path_provider=lambda: tmp_path / "priority.json")
    owner.load_roles({"角色": {
        "default_set": "暴击套", "default_weapon": "弧盘甲",
        "active_fork_crit_rate_bonus": 12.0, "likeability_crit_rate_bonus": .04,
        "suit_crit_source_known": True,
        "suit_permanent_crit_rates_by_name": {"暴击套": {2: .06, 4: .14}, "无暴击套": {2: 0, 4: 0}},
    }}, ["暴击套", "无暴击套"], weapons_db={
        "弧盘甲": {"sub_stats": {"暴击率%": 20.0}},
        "弧盘乙": {"sub_stats": {"暴击率%": 30.0}},
    })
    owner.selected = ["角色"]
    owner._set_crit_rate_cap("角色", 88.5)
    owner.stat_priority_configs["角色"] = {"crit_threshold": 37}
    yield app, owner
    owner.close()
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def edit_dialog(monkeypatch, owner, action, *, accept=True, initial_cap="88.5"):
    created = []

    def interact(dialog):
        created.append(dialog)
        sets = next(combo for combo in dialog.findChildren(SearchableComboBox) if combo.findData("暴击套") >= 0)
        weapons = next(combo for combo in dialog.findChildren(SearchableComboBox) if combo.findData("弧盘甲") >= 0)
        effect = next(combo for combo in dialog.findChildren(QComboBox) if combo.findData(FOUR_PIECE) >= 0)
        cap = next(edit for edit in dialog.findChildren(QLineEdit) if edit.text() == initial_cap)
        minimum = next(edit for edit in dialog.findChildren(QLineEdit) if edit.text() == "37")
        action(sets, weapons, effect, cap)
        assert minimum.text() == "37"
        return QDialog.Accepted if accept else QDialog.Rejected

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        owner._manage_role_preferences("角色")
    finally:
        for dialog in created:
            dialog.close()
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize("action,expected", [("set", 84.0), ("two", 78.0), ("none", 84.0), ("weapon", 52.0)])
def test_context_changes_refresh_dialog_and_saved_automatic_budget(selector, monkeypatch, action, expected):
    _app, owner = selector

    def interact(sets, weapons, effect, cap):
        if action == "set":
            sets.setCurrentText("无暴击套")
        elif action == "weapon":
            weapons.setCurrentText("弧盘乙")
        else:
            effect.setCurrentIndex(effect.findData(TWO_PIECE if action == "two" else "none"))
        assert cap.text() == f"{expected:g}"

    edit_dialog(monkeypatch, owner, interact)
    assert owner.crit_rate_caps["角色"] == expected
    assert owner.crit_rate_cap_sources["角色"] == "automatic"
    assert owner.stat_priority_configs["角色"]["crit_threshold"] == 37
    if action != "weapon":
        assert owner.custom_weapons == {}  # 只换套装不把账号实际弧盘升级成模板满级假定。


def test_latest_manual_edit_wins_but_later_suit_selection_restores_automatic(selector, monkeypatch):
    _app, owner = selector

    def interact(sets, _weapons, _effect, cap):
        cap.setText("0")
        cap.textEdited.emit("0")
        sets.setCurrentText("无暴击套")
        assert cap.text() == "84"
        cap.setText("65.5")
        cap.textEdited.emit("65.5")

    edit_dialog(monkeypatch, owner, interact)
    assert owner.crit_rate_cap_sources["角色"] == "manual"
    assert owner.get_crit_rate_caps() == {"角色": 65.5}


def test_suit_and_weapon_changes_use_the_entire_current_draft(selector, monkeypatch):
    _app, owner = selector

    def interact(sets, weapons, effect, cap):
        sets.setCurrentText("无暴击套")
        assert cap.text() == "84"
        weapons.setCurrentText("弧盘乙")
        assert cap.text() == "66"
        sets.setCurrentText("暴击套")
        assert cap.text() == "52"
        effect.setCurrentIndex(effect.findData(TWO_PIECE))
        assert cap.text() == "60"

    edit_dialog(monkeypatch, owner, interact)
    assert owner.crit_rate_caps["角色"] == 60.0
    # 界面预算扣套装；求解端会检查实际激活的套装，因此传输预算还原这一项，不能重复扣除。
    assert owner.get_crit_rate_caps() == {"角色": 66.0}


def test_cancel_keeps_all_saved_preferences_and_manual_source(selector, monkeypatch):
    _app, owner = selector
    before = deepcopy((owner.custom_sets, owner.custom_weapons, owner.set_effect_modes,
                       owner.crit_rate_caps, owner.crit_rate_cap_sources, owner.stat_priority_configs))

    def interact(sets, weapons, effect, cap):
        sets.setCurrentText("无暴击套")
        weapons.setCurrentText("弧盘乙")
        effect.setCurrentIndex(effect.findData("none"))
        assert cap.text() == "66"

    edit_dialog(monkeypatch, owner, interact, accept=False)
    assert (owner.custom_sets, owner.custom_weapons, owner.set_effect_modes,
            owner.crit_rate_caps, owner.crit_rate_cap_sources, owner.stat_priority_configs) == before


def test_unknown_suit_evidence_is_not_presented_as_zero(selector, monkeypatch):
    _app, owner = selector
    owner.all_roles["角色"]["suit_crit_source_known"] = False

    def interact(sets, _weapons, _effect, cap):
        sets.setCurrentText("无暴击套")
        assert cap.text() == ""

    edit_dialog(monkeypatch, owner, interact)
    assert owner.get_crit_rate_caps() == {"角色": None}


def test_confirm_without_context_changes_keeps_manual_cap(selector, monkeypatch):
    _app, owner = selector
    edit_dialog(monkeypatch, owner, lambda _s, _w, _e, cap: None)
    assert owner.crit_rate_caps == {"角色": 88.5}
    assert owner.crit_rate_cap_sources == {"角色": "manual"}


def test_automatic_cap_round_trips_with_suit_mode_and_solver_budget(selector, monkeypatch, tmp_path):
    _app, owner = selector

    def interact(_sets, _weapons, effect, cap):
        effect.setCurrentIndex(effect.findData(TWO_PIECE))
        assert cap.text() == "78"

    edit_dialog(monkeypatch, owner, interact)
    path = tmp_path / "saved.json"
    owner._write_priority_config(path)
    owner.crit_rate_caps.clear()
    owner.crit_rate_cap_sources.clear()
    owner._load_priority_config_from(path)
    assert owner.crit_rate_caps["角色"] == 78.0
    assert owner.crit_rate_cap_sources["角色"] == "automatic"
    assert owner._active_suit_crit_rate("角色") == 6.0
    assert owner.get_crit_rate_caps() == {"角色": 84.0}


def test_initial_automatic_cap_tracks_completed_suit_selection(selector, monkeypatch):
    _app, owner = selector
    owner.crit_rate_caps.clear()
    owner.crit_rate_cap_sources.clear()

    def interact(sets, _weapons, _effect, cap):
        sets.completer().activated[str].emit("无暴击套")
        assert cap.text() == "84"

    edit_dialog(monkeypatch, owner, interact, initial_cap="70")
    assert owner.crit_rate_caps == {"角色": 84.0}
    assert owner.crit_rate_cap_sources == {"角色": "automatic"}
