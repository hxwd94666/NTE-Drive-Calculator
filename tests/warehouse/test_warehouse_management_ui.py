# 验证独立仓库窗口的两区交互、统一保存后直接预览、取消与账号代次，不触及真实账号或游戏。
import os
from copy import deepcopy
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect, QSize, QPoint, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox, QPushButton, QWidget, QLabel

from src.domain.equipment.warehouse.intelligent_rules import attach_origin
from src.domain.equipment.eligibility.management_settings import MatchingScope, normalize_warehouse_config
from src.domain.equipment.warehouse.warehouse_rule_sections import RuleSection
from src.domain.localization.catalog import make_catalog
from src.features.inventory.warehouse.management import management_controller as module
from src.features.inventory.warehouse.management.management_dialog import WarehouseManagementDialog
from src.features.inventory.warehouse.management.management_projection import (
    WarehouseDraftInput,
    project_management_preview,
    capture_management_input,
    refresh_conflict_summary,
)
from src.integrations.preferences.locale_files import bundled_text_catalog, read_catalog_file
from src.app.theme import apply_app_theme, current_theme_name
from src.app.window_geometry import fit_dialog_to_available_screen
from src.services.inventory.management.management_settings import ManagementSettingsService
from src.services.inventory.recommendation.warehouse_recommendation_preview import RecommendationGuard
from src.features.inventory.warehouse.progress import update_warehouse_save_state
from src.storage.sqlite.user_data_dao import UserDataDao


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    if os.name == "nt" and "Microsoft YaHei UI" not in QFontDatabase.families():
        for name in ("msyh.ttc", "msyhbd.ttc"):
            path = Path(os.environ["WINDIR"]) / "Fonts" / name
            if path.is_file():
                QFontDatabase.addApplicationFontFromData(path.read_bytes())
    return app


def wait(app, condition):
    deadline = monotonic() + 5
    while not condition() and monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert condition()


def editor_data(config=None, matching=None):
    config = normalize_warehouse_config(config)
    matching = matching or {"version": 1, "extra_suits": {}}
    data = (((1, "角色", "Suit1"),), (("Suit1", "套装"),), matching, matching, {"roles": {}, "suits": {}})
    return config, matching, data, ([("H_3", 3)], ["套装"], [(1, "角色", "", False)])


def ordinary(identity, intelligent=False):
    value = {
        "rule_id": identity,
        "enabled": True,
        "name": identity,
        "main_stats": ["生命值%"],
        "item_type": "tape",
        "action": "keep",
    }
    return attach_origin(value, "combo:test") if intelligent else value


def retire(dialog):
    QDialog.done(dialog, QDialog.Rejected)
    dialog.deleteLater()


def test_successful_generation_enables_skip_and_manual_choice_keeps_thresholds(app, monkeypatch):
    dialog = WarehouseManagementDialog(bundled_text_catalog(), config_dir=Path("config"))
    try:
        dialog.install(editor_data())
        dialog.show_rules()
        dialog.show()
        app.processEvents()
        before, _ = dialog.collect()
        assert not dialog.rules.skip_scoring.isChecked()
        assert not dialog.rules.skip_scoring.isEnabled()
        assert dialog.rules.skip_scoring_help.isEnabled()
        dialog.rules.install_generated({"options": {}, "rules": [ordinary("generated", True)]})
        collected, _ = dialog.collect()
        assert collected["skip_scoring"] is True
        assert dialog.rules.skip_scoring.isEnabled()
        assert not dialog._scoring_page.isEnabled()
        assert dialog.tabs.tabText(0) == dialog.text("warehouse.management.scoring_skipped")
        assert collected["lock"] == before["lock"] and collected["discard"] == before["discard"]
        dialog.rules.skip_scoring.setFocus()
        QTest.keyClick(dialog.rules.skip_scoring, Qt.Key_Space)
        assert dialog.collect()[0]["skip_scoring"] is False
        assert dialog._scoring_page.isEnabled()
        # 生成结果不合法时，不提交半份规则，也不擅自重新开启跳过。
        existing = deepcopy(dialog.rules.rules)
        with pytest.raises(ValueError):
            dialog.rules.install_generated({"options": {}, "rules": [ordinary("not-intelligent")]})
        assert dialog.rules.rules == existing and not dialog.rules.skip_scoring.isChecked()
        seen = []

        def show_help(box):
            seen.append((box.text(), box.textFormat()))
            return QMessageBox.Ok

        monkeypatch.setattr(QMessageBox, "exec", show_help)
        dialog.rules.skip_scoring_help.click()
        assert seen == [(dialog.text("warehouse.management.rules.skip_scoring_help"), Qt.PlainText)]
        assert dialog.rules.skip_scoring_help.accessibleName()
        dialog.rules.clear_intelligent()
        assert not dialog.rules.skip_scoring.isEnabled()
        assert not dialog.rules.skip_scoring.isChecked()
        assert dialog.collect()[0]["skip_scoring"] is False
        assert dialog._scoring_page.isEnabled()
    finally:
        retire(dialog)


def test_separate_sections_copy_to_manual_tail_and_disallow_cross_section_drag(app):
    config = {"preserve_rules": [ordinary("a"), ordinary("b"), ordinary("smart", True)]}
    original = deepcopy(config)
    dialog = WarehouseManagementDialog(bundled_text_catalog(), config_dir=Path("config"))
    try:
        dialog.install(editor_data(config))
        dialog.tabs.setCurrentIndex(1)
        dialog.show()
        app.processEvents()
        rules = dialog.rules
        smart_row = rules.rows[RuleSection.INTELLIGENT][0][1]
        copy = next(b for b in smart_row.findChildren(QPushButton) if b.accessibleName() == "复制")
        copy.click()
        assert [row["name"] for row in rules.rules] == ["a", "b", "smart 副本", "smart"]
        assert not rules.rules[2].get("intelligent") and rules.rules[3]["intelligent"]
        assert config == original
        current = deepcopy(rules.rules)
        first = rules.rules[0]
        destination = rules.sections[RuleSection.INTELLIGENT].mapToGlobal(
            rules.sections[RuleSection.INTELLIGENT].rect().center()
        )
        rules.orders[RuleSection.MANUAL].drop(first, destination)
        assert rules.rules == current
        rules.orders[RuleSection.MANUAL].move(first, -1)
        assert rules.rules == current
        rules.orders[RuleSection.MANUAL].move(first, 1)
        assert [row["name"] for row in rules.rules] == ["b", "a", "smart 副本", "smart"]
        rules.clear.click()
        assert [row["name"] for row in rules.rules] == ["b", "a", "smart 副本"]
        collected, _ = dialog.collect()
        assert "discard_unmatched" not in collected["discard"]
        assert set(dialog._widgets["discard"]).isdisjoint({"discard_unmatched"})
    finally:
        retire(dialog)


def context(tmp_path):
    path = tmp_path / "user.sqlite3"
    with UserDataDao(path, account_id="test"):
        pass
    return SimpleNamespace(
        account=SimpleNamespace(active_account_id="test", user_database_path=path),
        generation=1,
        paths=SimpleNamespace(
            config_dir=tmp_path,
            equipment_allocation_database_path=tmp_path / "static.sqlite3",
            equipment_allocation_asset_root=tmp_path / "images",
        ),
        register_account_lifecycle=lambda owner: lambda: None,
    )


def owner(app, tmp_path, monkeypatch, **callbacks):
    ctx = context(tmp_path)
    service = ManagementSettingsService(ctx.account.user_database_path, scope=MatchingScope.WAREHOUSE)
    config, matching = service.load()
    monkeypatch.setattr(module, "load_warehouse_editor", lambda *_: editor_data(config, matching))
    widget = QWidget()
    controller = module.WarehouseManagementController(
        ctx,
        bundled_text_catalog(),
        widget,
        capture=callbacks.get("capture", lambda: WarehouseDraftInput(7, (("manual", "normal"),))),
        project=callbacks.get("project", Mock()),
        settings_changed=callbacks.get("settings_changed", Mock()),
    )
    controller.open()
    wait(app, lambda: controller._expected is not None and not controller.is_running())
    return widget, controller, service


def test_generate_saves_all_settings_then_projects_once_without_adoption_window(app, tmp_path, monkeypatch):
    calls, contexts = [], []
    project = lambda *args: calls.append(("project", args))
    widget, controller, service = owner(
        app, tmp_path, monkeypatch, project=project, settings_changed=lambda: calls.append(("saved", service.load()))
    )
    scan = ManagementSettingsService(service.database, scope=MatchingScope.SCAN).load()

    def build(**kwargs):
        calls.append(("build", service.load()))
        contexts.append(kwargs["check_context"])
        assert kwargs["user_targets"] == {"manual": "normal"}
        return {"rows": [], "snapshot_id": 7}, SimpleNamespace(snapshot_id=7)

    monkeypatch.setattr(module, "build_preview", build)
    try:
        controller.dialog._widgets["lock"]["grade"].setCurrentText("ACE")
        controller.dialog.buttons.button(QDialogButtonBox.Ok).click()
        wait(app, lambda: controller.dialog is None and not controller.is_running())
        assert [call[0] for call in calls] == ["saved", "build", "project"]
        assert service.load()[0]["lock"]["grade"] == "ACE"
        assert ManagementSettingsService(service.database, scope=MatchingScope.SCAN).load() == scan
        # 预览证据绑定账号身份，而不是已关闭编辑器的取消标志。
        contexts[0]()
        assert controller.dialog is None
    finally:
        controller.stop()
        wait(app, lambda: not controller.is_running())
        widget.deleteLater()


def test_preview_failure_keeps_saved_settings_and_can_retry_without_overwriting_scan(app, tmp_path, monkeypatch):
    widget, controller, service = owner(app, tmp_path, monkeypatch)
    calls = []

    def fail(**kwargs):
        calls.append(True)
        raise ValueError("证据不足")

    monkeypatch.setattr(module, "build_preview", fail)
    try:
        controller.dialog._widgets["lock"]["grade"].setCurrentText("ACE")
        controller.generate()
        wait(app, lambda: len(calls) == 1 and not controller.is_running())
        assert service.load()[0]["lock"]["grade"] == "ACE"
        assert controller.dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
        assert "证据不足" in controller.dialog.status.text()
        controller.generate()
        wait(app, lambda: len(calls) == 2 and not controller.is_running())
        assert service.load()[0]["lock"]["grade"] == "ACE"
    finally:
        controller.stop()
        wait(app, lambda: controller.dialog is None and not controller.is_running())
        widget.deleteLater()


def test_cancel_and_account_change_wait_for_owned_read_and_never_deliver_late_result(app, tmp_path, monkeypatch):
    ctx, started, release = context(tmp_path), Event(), Event()

    def read(request, cancel):
        started.set()
        release.wait(3)
        return editor_data()

    monkeypatch.setattr(module, "load_warehouse_editor", read)
    widget, project = QWidget(), Mock()
    controller = module.WarehouseManagementController(
        ctx,
        bundled_text_catalog(),
        widget,
        capture=lambda: WarehouseDraftInput(7, ()),
        project=project,
        settings_changed=Mock(),
    )
    try:
        controller.open()
        wait(app, started.is_set)
        ctx.generation += 1
        controller.stop()
        assert controller.is_running() and controller.dialog is not None
        release.set()
        wait(app, lambda: not controller.is_running() and controller.dialog is None)
        project.assert_not_called()
    finally:
        release.set()
        controller.stop()
        wait(app, lambda: not controller.is_running())
        widget.deleteLater()


def test_direct_projection_preserves_manual_no_change_and_replaces_old_generated_targets():
    guard = SimpleNamespace(snapshot_id=7, require_current=Mock(), require_targets=Mock())
    controller = SimpleNamespace(
        manual_targets={"manual": "normal", "protected": "discarded"},
        recommendation_targets={"old": "locked"},
        recommendation_guard=object(),
        conflicts=[],
        cancel_quality=Mock(),
        score_label=None,
    )
    host = SimpleNamespace(
        _warehouse_snapshot_id=7,
        _warehouse_source="nte_core",
        warehouse_read_controller=controller,
        _warehouse_base_states={"manual": "normal", "old": "normal", "new": "normal", "protected": "normal"},
        _warehouse_pending_state_changes={"old": "locked", "protected": "discarded"},
        _warehouse_all_items=[
            {"uid": uid, "locked": uid == "old", "discarded": uid == "protected"}
            for uid in ("manual", "old", "new", "protected")
        ]
        + [{"uid": "unknown", "locked": None, "discarded": None, "state_known": False}],
        _apply_warehouse_filters=Mock(),
        _update_warehouse_save_state=Mock(),
        text_catalog=bundled_text_catalog(),
    )
    expected = capture_management_input(host)
    result = {
        "rows": [
            {
                "uid": "new",
                "decision": "discard",
                "original_locked": False,
                "original_equipped": False,
                "original_discarded": False,
            },
            {
                "uid": "unknown",
                "decision": "review",
                "decision_status": "unknown",
                "reasons": ["state_unknown"],
                "state_known": False,
            },
            {"uid": "protected", "decision": "review", "predecessor_fixed": True,
             "decision_status": "blocked", "reasons": ["state_protected"]},
        ],
        "conflicts": [{"uid": "manual"}, {"uid": "rule-conflict"}],
    }
    project_management_preview(host, expected, result, guard)
    assert host._warehouse_pending_state_changes == {"new": "discarded"}
    assert controller.manual_targets == {"manual": "normal", "protected": "discarded"} and controller.recommendation_targets == {
        "new": "discarded"
    }
    assert controller.conflicts == [{"uid": "rule-conflict"}]
    assert controller.management_issues == result["rows"][1:]
    assert not next(row for row in host._warehouse_all_items if row["uid"] == "old")["locked"]
    assert not next(row for row in host._warehouse_all_items if row["uid"] == "protected")["discarded"]
    assert next(row for row in host._warehouse_all_items if row["uid"] == "unknown")["locked"] is None
    snapshot = deepcopy(host._warehouse_all_items)
    controller.manual_targets["manual"] = "locked"
    with pytest.raises(ValueError, match="人工草稿"):
        project_management_preview(host, expected, result, guard)
    assert host._warehouse_all_items == snapshot


def test_zero_pending_changes_keep_preview_witness_for_later_manual_edits():
    guard = RecommendationGuard(
        Path("unused"), 7, "frozen", (), (), {}, {}, lambda: None, required_retained_uids=frozenset({"needed"})
    )
    owner = SimpleNamespace(recommendation_guard=guard, recommendation_targets={"old": "locked"}, closed=False)
    host = SimpleNamespace(warehouse_read_controller=owner, _warehouse_pending_state_changes={})
    # 把全部自动变更撤回不等于结束本轮预览；随后手工弃置仍受同一见证约束。
    update_warehouse_save_state(host)
    assert owner.recommendation_guard is guard
    host._warehouse_pending_state_changes = {"needed": "discarded"}
    update_warehouse_save_state(host)
    with pytest.raises(ValueError, match="未保留"):
        owner.recommendation_guard.require_targets(host._warehouse_pending_state_changes)


def test_unknown_and_blocked_reasons_are_visible_without_creating_targets(app):
    label = QLabel()
    owner = SimpleNamespace(
        conflicts=[],
        issue_label=label,
        management_issues=[
            {"uid": "unknown", "decision_status": "unknown", "reasons": ["state_unknown"]},
            {"uid": "protected", "decision_status": "blocked", "reasons": ["state_protected"]},
            {"uid": "incomplete", "decision_status": "unknown", "reasons": ["evaluation_incomplete"]},
        ],
    )
    host = SimpleNamespace(
        warehouse_read_controller=owner, text_catalog=bundled_text_catalog(), _warehouse_pending_state_changes={}
    )
    try:
        refresh_conflict_summary(host)
        assert label.isHidden() and not label.text()
        assert host._warehouse_pending_state_changes == {}
        owner.management_issues = []
        refresh_conflict_summary(host)
        assert label.isHidden() and not label.text()
    finally:
        label.deleteLater()


def test_language_patch_fallback_and_new_keys_preserve_parameters(app):
    base = bundled_text_catalog()
    old = make_catalog(
        {
            "format_version": 1,
            "catalog_id": "nte-ui",
            "locale": "en-US",
            "display_name": "Test",
            "messages": {"matching.save": "Save"},
        },
        base=base,
    )
    assert old.render("warehouse.management.preview") == "生成预览"
    sample = read_catalog_file(Path("docs/examples/locales/qps-ploc.json"))
    patched = make_catalog(sample, base=base)
    required = {
        key for key in base.messages if key.startswith(("warehouse.management.", "scan.management.", "matching.scope."))
    }
    assert required <= sample["messages"].keys()
    assert all(patched.parameters[key] == base.parameters[key] for key in required)
    dialog = WarehouseManagementDialog(patched, config_dir=Path("config"))
    try:
        dialog.install(editor_data())
        dialog.show()
        app.processEvents()
        assert dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
        assert dialog.buttons.button(QDialogButtonBox.Ok).accessibleName() == patched.render(
            "warehouse.management.preview"
        )
        assert dialog.status.textFormat() == Qt.PlainText
        assert dialog.buttons.geometry().right() <= dialog.width()
        assert (
            dialog.buttons.button(QDialogButtonBox.Ok)
            .mapToGlobal(dialog.buttons.button(QDialogButtonBox.Ok).rect().center())
            .x()
            > dialog.buttons.button(QDialogButtonBox.Cancel)
            .mapToGlobal(dialog.buttons.button(QDialogButtonBox.Cancel).rect().center())
            .x()
        )
    finally:
        retire(dialog)


@pytest.mark.parametrize("theme", ["black"])
def test_editor_actions_remain_reachable_with_long_patch_on_small_screen(app, theme):
    previous = current_theme_name(app)
    apply_app_theme(app, theme)
    catalog = make_catalog(read_catalog_file(Path("docs/examples/locales/qps-ploc.json")), base=bundled_text_catalog())
    dialog = WarehouseManagementDialog(catalog, config_dir=Path("config"))
    try:
        dialog.install(editor_data({"preserve_rules": [ordinary("manual"), ordinary("intelligent", True)]}))
        dialog.show()
        fit_dialog_to_available_screen(dialog, QSize(680, 540), available_geometry=QRect(0, 0, 740, 620))
        for index in range(3):
            dialog.tabs.setCurrentIndex(index)
            app.processEvents()
            if index == 1:
                for control in (dialog.rules.skip_scoring, dialog.rules.skip_scoring_help,
                                dialog.rules.generate, dialog.rules.clear):
                    dialog.rules.scroll.ensureWidgetVisible(control)
                    app.processEvents()
                    point = control.mapTo(dialog, QPoint())
                    assert control.isVisible() and control.isEnabled()
                    assert 0 <= point.x() and point.x() + control.width() <= dialog.width()
                    assert 0 <= point.y() and point.y() + control.height() <= dialog.height()
            for role in (QDialogButtonBox.Cancel, QDialogButtonBox.Ok):
                button = dialog.buttons.button(role)
                point = button.mapTo(dialog, QPoint())
                assert button.isVisible() and button.isEnabled()
                assert 0 <= point.x() and point.x() + button.width() <= dialog.width()
                assert 0 <= point.y() and point.y() + button.height() <= dialog.height()
                if role == QDialogButtonBox.Ok:
                    assert point.x() + button.width() > dialog.width() * 0.9
        dialog.matching._open_role(dialog.matching.role_list.item(0))
        app.processEvents()
        assert dialog.matching.buttons.button(QDialogButtonBox.Save).isVisible()
        dialog.matching.buttons.button(QDialogButtonBox.Save).click()
        assert dialog.matching.stack.currentIndex() == 0
        QTest.keyClick(dialog, Qt.Key_Tab)
        assert dialog.focusWidget() is not None
    finally:
        retire(dialog)
        apply_app_theme(app, previous)


@pytest.mark.parametrize("theme", ["black"])
@pytest.mark.parametrize("long_patch", [False, True])
def test_rule_card_actions_remain_reachable_on_narrow_screen(app, theme, long_patch):
    previous = current_theme_name(app)
    apply_app_theme(app, theme)
    base = bundled_text_catalog()
    catalog = (
        make_catalog(read_catalog_file(Path("docs/examples/locales/qps-ploc.json")), base=base) if long_patch else base
    )
    dialog = WarehouseManagementDialog(catalog, config_dir=Path("config"))
    try:
        dialog.install(editor_data({"preserve_rules": [ordinary("manual"), ordinary("smart", True)]}))
        dialog.show_rules()
        dialog.show()
        fit_dialog_to_available_screen(dialog, QSize(680, 540), available_geometry=QRect(0, 0, 740, 620))
        app.processEvents()
        assert dialog.rules.scroll.horizontalScrollBar().maximum() == 0
        for section in RuleSection:
            row = dialog.rules.rows[section][0][1]
            for action in ("edit", "copy", "delete"):
                button = next(
                    button
                    for button in row.findChildren(QPushButton)
                    if button.accessibleName() == catalog.render("warehouse.management.rules." + action)
                )
                dialog.rules.scroll.ensureWidgetVisible(button)
                app.processEvents()
                assert button.isVisible() and button.isEnabled()
                assert button.text().strip()
                point = button.mapTo(dialog.rules.scroll.viewport(), QPoint())
                assert dialog.rules.scroll.viewport().rect().contains(QRect(point, button.size()))
                button.setFocus(Qt.TabFocusReason)
                assert button.hasFocus()
        assert dialog.collect()[0]["preserve_rules"][0]["rule_id"] == "manual"
    finally:
        retire(dialog)
        apply_app_theme(app, previous)


def test_rule_handle_restores_identity_focus_after_keyboard_reorder(app):
    dialog = WarehouseManagementDialog(bundled_text_catalog(), config_dir=Path("config"))
    try:
        dialog.install(editor_data({"preserve_rules": [ordinary(name) for name in ("a", "b", "c")]}))
        dialog.show_rules()
        dialog.show()
        app.processEvents()
        dialog.rules.focus_rule("a")
        QTest.keyClick(dialog.rules._handles["a"], Qt.Key_Down, Qt.ControlModifier)
        wait(app, lambda: dialog.rules._handles["a"].hasFocus())
        assert [row["rule_id"] for row in dialog.rules.rules] == ["b", "a", "c"]
        QTest.keyClick(dialog.rules._handles["a"], Qt.Key_Down, Qt.ControlModifier)
        wait(app, lambda: dialog.rules._handles["a"].hasFocus())
        assert [row["rule_id"] for row in dialog.rules.rules] == ["b", "c", "a"]
    finally:
        retire(dialog)
