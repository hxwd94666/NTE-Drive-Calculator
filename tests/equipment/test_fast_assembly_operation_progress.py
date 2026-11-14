# 验证极速装配操作进度独立于后验确认，窗口关闭停止任务且不重新弹出。
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication, QWidget
from src.domain.equipment.assembly.assembly_progress import AssemblyTarget
from src.features.inventory.equipment.assembly.assembly_surface import AssemblySurface
from src.services.equipment.assembly.assembly_progress import apply_progress_event

NTE_TEST_TIER = "core"


@pytest.mark.parametrize("automatic", [False, True])
def test_dispatch_progress_and_native_close_preserve_confirmation_gate(automatic):
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    surface = AssemblySurface(owner)
    try:
        surface.configure_targets(
            (AssemblyTarget(1, None, "A", "main", 230), AssemblyTarget(2, None, "B", "main", 210)), automatic=automatic
        )
        surface.begin_running("Assembly", "Running")
        app.processEvents()
        assert surface.stop_button.isVisible() == automatic
        for slot_id in (1, 2):
            apply_progress_event(surface.progress_model, dict(slot_id=slot_id, state="waiting", dispatched=True))
        surface.render_progress()
        assert surface.progress_bar.value() == (0 if automatic else 2)
        assert surface.progress_model.confirmed == 0
        stopped = []
        surface.stop_requested.connect(lambda: stopped.append(True))
        surface.close()
        app.processEvents()
        assert stopped == [True] and not surface.isVisible()
        surface.show_result("Pending", "Awaiting observation")
        assert not surface.isVisible() and surface.progress_model.confirmed == 0
        assert surface.close_button.isVisible() is False
    finally:
        surface.running = False
        surface.close()
        surface.deleteLater()
        owner.close()
        owner.deleteLater()
        app.processEvents()


def test_fast_result_has_no_footer_and_escape_closes():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    surface = AssemblySurface()
    try:
        surface.configure_targets(())
        surface.show_result("Result", "Summary")
        app.processEvents()
        assert not surface.stop_button.isVisible() and not surface.close_button.isVisible()
        QTest.keyClick(surface, Qt.Key_Escape)
        app.processEvents()
        assert not surface.isVisible()
    finally:
        surface.close()
        surface.deleteLater()
        app.processEvents()
