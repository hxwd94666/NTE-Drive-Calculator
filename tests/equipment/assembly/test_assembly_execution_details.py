# 验证两种装配共用详情折叠栏的键盘操作、译文回退和摘要去重，不固定装饰像素。
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from src.app.theme import apply_app_theme, current_theme_name
from src.domain.equipment.assembly.assembly_progress import AssemblyTarget
from src.domain.localization.catalog import make_catalog
from src.features.inventory.equipment.assembly.assembly_surface import AssemblySurface
from src.integrations.preferences.locale_files import bundled_text_catalog
from tests.support.qt_cleanup import dispose_windows_created_by_test as dispose_windows_created_by_test

NTE_TEST_TIER = "core"


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("theme", ["black"])
def test_details_toggle_handles_whole_row_space_and_enter_without_closing(app, monkeypatch, automatic, theme):
    from src.app import window_geometry

    monkeypatch.setattr(window_geometry, "_available_geometry", lambda _: QRect(0, 0, 640, 480))
    previous = current_theme_name()
    apply_app_theme(app, theme)
    catalog = make_catalog({
        "format_version": 1, "catalog_id": "nte-ui", "locale": "en-US", "display_name": "Test",
        "messages": {"assembly.status.details": "<b>Execution details</b> " + "Long translation " * 8},
    }, base=bundled_text_catalog())
    owner = QWidget()
    surface = AssemblySurface(owner, catalog=catalog)
    try:
        surface.configure_targets((AssemblyTarget(1, None, "fixture", "primary", 298.08),), automatic=automatic)
        rows = surface.progress_model.rows
        summary = "Processed 1 role."
        diagnostic = "Other roster item was not recognized; target is unaffected."
        surface.show_result("Result", summary + "\n" + diagnostic)
        app.processEvents()
        view = surface.status_view
        button = view.details_toggle
        assert button.isVisible() and not button.isChecked()
        assert not view.details.isVisible()
        assert view.details.text() == diagnostic
        assert button.accessibleName() == catalog.render("assembly.status.details")
        assert view.details_title.textFormat() == Qt.PlainText
        # 点击行的右端而不是小标题，仍切换同一个折叠动作。
        QTest.mouseClick(button, Qt.LeftButton, pos=QPoint(button.rect().right() - 6, button.rect().center().y()))
        app.processEvents()
        assert button.isChecked() and view.details.isVisible()
        button.setFocus()
        QTest.keyClick(button, Qt.Key_Space)
        app.processEvents()
        assert not button.isChecked() and not view.details.isVisible()
        QTest.keyClick(button, Qt.Key_Return)
        app.processEvents()
        assert button.isChecked() and view.details.isVisible() and surface.isVisible()
        assert surface.progress_model.rows is rows
        close = surface.close_button
        assert close.isVisible() == automatic
        if automatic:
            assert surface.rect().contains(close.mapTo(surface, close.rect().center()))
        assert button.rect().contains(view.details_arrow.mapTo(button, view.details_arrow.rect().center()))
    finally:
        surface.close()
        surface.deleteLater()
        owner.close()
        owner.deleteLater()
        app.processEvents()
        apply_app_theme(app, previous)


def test_only_summary_has_no_empty_disclosure_and_long_summary_keeps_full_text(app):
    owner = QWidget()
    surface = AssemblySurface(owner)
    try:
        surface.configure_targets(())
        surface.show_result("Result", "Summary only")
        app.processEvents()
        assert not surface.status_view.details_toggle.isVisible()
        assert surface.status_view.details.text() == ""
        full = "Long summary " * 30
        surface.show_result("Result", full)
        app.processEvents()
        assert surface.status_view.details_toggle.isVisible()
        assert surface.status_view.details.text() == full.strip()
        assert surface.status_view.details_toggle.accessibleName() == "执行详情"
    finally:
        surface.close()
        surface.deleteLater()
        owner.close()
        owner.deleteLater()
        app.processEvents()
