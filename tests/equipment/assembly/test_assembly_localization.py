# 验证装配文本目录的显式传递、旧补丁回退、纯文本和长译文下的可操作性。
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QWidget

from src.app.theme import apply_app_theme, current_theme_name
from src.domain.localization.catalog import CatalogError, make_catalog
from src.features.inventory.equipment.assembly.assembly_dialogs import assembly_report_dialog
from src.features.inventory.equipment.assembly.automatic_assembly_dialog import AutomaticAssemblyDialog
from src.features.inventory.equipment.management.batch_selection_dialog import BatchChoice, BatchSelectionDialog
from src.integrations.preferences.locale_files import bundled_text_catalog, read_catalog_file
from src.domain.equipment.assembly.assembly_progress import AssemblyTarget
from src.features.inventory.equipment.assembly.assembly_surface import AssemblySurface
from tests.support.qt_cleanup import dispose_windows_created_by_test as dispose_windows_created_by_test

NTE_TEST_TIER = "core"
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def patch(**messages):
    return make_catalog(
        {"format_version": 1, "catalog_id": "nte-ui", "locale": "en-US", "display_name": "Test", "messages": messages},
        base=bundled_text_catalog(),
    )


def test_old_partial_patch_falls_back_without_changing_named_parameter_contract():
    catalog = patch(**{"settings.language.save": "Confirm language"})
    assert catalog.render("assembly.fast.start") == "开始极速装配"
    assert catalog.render("assembly.auto.summary", count=3) == "将自动装配 3 名角色 · 每人一套计算配装"
    with pytest.raises(CatalogError, match="parameter_mismatch"):
        patch(**{"assembly.auto.summary": "Selected {roles}"})
    source = read_catalog_file(ROOT / "docs/examples/locales/qps-ploc.json")
    example = make_catalog(source, base=bundled_text_catalog())
    assert example.parameters == bundled_text_catalog().parameters
    required = {
        key
        for key in bundled_text_catalog().messages
        if key.startswith(("assembly.", "selector.", "batch.", "common."))
    }
    assert required <= source["messages"].keys()


def test_translated_picker_keeps_ids_scores_sort_and_literal_text(app):
    catalog = patch(
        **{
            "selector.sort_desc": "<b>Highest first</b>",
            "selector.selected_count": "Selected {count}",
            "common.cancel": "Cancel",
        }
    )
    dialog = BatchSelectionDialog(
        None,
        [BatchChoice(41, "<b>角色</b>", score=20), BatchChoice(52, "角色二", score=40)],
        title="Test",
        description="",
        accept_text="Start",
        show_hints=False,
        catalog=catalog,
    )
    try:
        dialog.show()
        app.processEvents()
        assert dialog.text_catalog is catalog
        assert dialog.sort_selector.currentData() == "score_desc"
        assert dialog.sort_selector.itemText(0) == "<b>Highest first</b>"
        assert dialog.grid.itemAtPosition(0, 0).widget().choice.key == 52
        assert dialog._cards[0].name_label.textFormat() == Qt.PlainText
        dialog.set_all(True)
        assert dialog.selected_ids() == [41, 52] and dialog.count.text() == "Selected 2"
        assert dialog.buttons.button(QDialogButtonBox.Cancel).accessibleName() == "Cancel"
    finally:
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("theme", ["black"])
@pytest.mark.parametrize("kind", ["fast", "automatic"])
def test_long_patch_keeps_start_cancel_back_and_search_reachable(app, monkeypatch, theme, kind):
    from src.app import window_geometry

    available = QRect(0, 0, 640, 480)
    monkeypatch.setattr(window_geometry, "_available_geometry", lambda _owner: available)
    catalog = make_catalog(read_catalog_file(ROOT / "docs/examples/locales/qps-ploc.json"), base=bundled_text_catalog())
    original = current_theme_name()
    apply_app_theme(app, theme)
    owner = QWidget()
    if kind == "automatic":
        dialog = AutomaticAssemblyDialog(owner, 3, stop_hotkey="F8", needs_name=True, medium=True, catalog=catalog)
        dialog.name_edit.setText("演示主角")
        actions = [dialog.back_button, dialog.start_button]
    else:
        dialog = BatchSelectionDialog(
            owner,
            [BatchChoice(41, "角色", score=None)],
            title=catalog.render("assembly.fast.title"),
            description="",
            accept_text=catalog.render("assembly.fast.start"),
            show_hints=False,
            catalog=catalog,
        )
        dialog.set_all(True)
        actions = [dialog.search, *dialog.buttons.buttons(), dialog.sort_selector]
    try:
        dialog.show()
        app.processEvents()
        window_geometry.fit_dialog_to_available_screen(dialog)
        app.processEvents()
        assert available.contains(dialog.frameGeometry())
        for action in actions:
            assert action.isVisible() and action.isEnabled()
            rectangle = QRect(action.mapTo(dialog, action.rect().topLeft()), action.size())
            assert dialog.rect().contains(rectangle)
        if kind == "automatic":
            assert any("F8" in label.text() for label in dialog.findChildren(QLabel))
            assert dialog.summary.textFormat() == Qt.PlainText
        else:
            assert dialog.scroll.horizontalScrollBar().maximum() == 0
            assert dialog._cards[0].score_label.accessibleName() == catalog.render("selector.score_unknown")
    finally:
        dialog.reject()
        dialog.deleteLater()
        owner.close()
        owner.deleteLater()
        app.processEvents()
        apply_app_theme(app, original)


def test_report_translation_changes_only_presentation_not_completion():
    catalog = patch(**{"assembly.report.summary": "Equipped {count}; actions {actions}"})
    report = SimpleNamespace(role_reports=[object()], executed_actions=12)
    _title, message, complete = assembly_report_dialog("Test", report, 1, catalog)
    assert complete and message == "Equipped 1; actions 12"
    report.missing_roles = ["角色"]
    assert not assembly_report_dialog("Test", report, 1, catalog)[2]


def test_automatic_sections_render_literal_translations_and_describe_required_name(app):
    catalog = patch(
        **{
            "assembly.auto.medium_hint": "<b>Suggested mode</b>",
            "assembly.auto.duplicate_hint": "Check duplicate drives",
            "assembly.auto.preparation_title": "Preparation",
            "assembly.auto.prepare_stop": "Press {hotkey} to stop subsequent actions",
            "assembly.auto.protagonist_help": "Use the player name shown in game",
            "assembly.auto.protagonist_required": "Required",
        }
    )
    dialog = AutomaticAssemblyDialog(None, 3, stop_hotkey="F8", needs_name=True, medium=True, catalog=catalog)
    try:
        labels = dialog.findChildren(QLabel)
        assert {
            "<b>Suggested mode</b>",
            "Check duplicate drives",
            "Preparation",
            "Required",
            "Press F8 to stop subsequent actions",
        } <= {label.text() for label in labels}
        assert all(label.textFormat() == Qt.PlainText for label in labels)
        assert dialog.name_edit.accessibleName() == catalog.render("assembly.auto.protagonist_label")
        assert dialog.name_edit.accessibleDescription() == "Use the player name shown in game"
        assert not dialog.start_button.isEnabled()
        dialog.name_edit.setText("演示名字")
        assert dialog.start_button.isEnabled()
    finally:
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("theme", ["black"])
def test_result_first_show_has_final_geometry_and_long_patch_actions_remain_reachable(app, theme):
    original = current_theme_name()
    apply_app_theme(app, theme)
    catalog = patch(
        **{
            "assembly.status.role_slot": "<b>{role}</b> · " + "Named slot " * 8 + "{slot}",
            "assembly.status.counts": "Processed {dispatched}/{total}; checked {confirmed}; partial {partial}",
        }
    )
    owner = QWidget()
    surface = AssemblySurface(owner, catalog=catalog)
    first_sizes = []

    class FirstShow(QObject):
        def eventFilter(self, watched, event):
            if watched is surface and event.type() == QEvent.Show:
                first_sizes.append(surface.size())
            return False

    probe = FirstShow(surface)
    surface.installEventFilter(probe)
    try:
        surface.configure_targets(tuple(AssemblyTarget(index, None, f"角色{index}", "备用", 230) for index in range(8)))
        surface.resize(20, 20)
        surface.show_result("结果待核对", "已停止后续操作。\n" + "诊断信息 " * 100)
        app.processEvents()
        assert first_sizes and first_sizes[0] == surface.size()
        assert surface.screen().availableGeometry().contains(surface.frameGeometry())
        close = surface.close_button
        assert not close.isVisible()
        labels = surface.findChildren(QLabel)
        assert any("<b>角色0</b>" in label.text() and label.textFormat() == Qt.PlainText for label in labels)
        assert surface.progress_model.confirmed == 0
    finally:
        surface.close()
        surface.deleteLater()
        owner.close()
        owner.deleteLater()
        app.processEvents()
        apply_app_theme(app, original)


@pytest.mark.parametrize("show_hints", [False, True])
def test_constructing_batch_picker_never_shows_an_unowned_top_level_window(app, show_hints):
    owner = QWidget()
    shown = []

    class TopLevelShows(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Show and isinstance(watched, QWidget) and watched.isWindow():
                shown.append(watched.metaObject().className())
            return False

    probe = TopLevelShows()
    app.installEventFilter(probe)
    dialog = None
    try:
        # 构造阶段不应映射任何顶层窗口；先显示无 owner 标签再塞进布局也算失败。
        dialog = BatchSelectionDialog(
            owner,
            [BatchChoice(1, "角色", character_id=1003)],
            title="装配选择",
            description="操作说明",
            accept_text="下一步",
            show_hints=show_hints,
        )
        assert shown == []
    finally:
        app.removeEventFilter(probe)
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        owner.close()
        owner.deleteLater()
        app.processEvents()
