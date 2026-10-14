# 验证隐藏战报控件的窗口归属、悬浮窗复用和编辑器换代后的物理释放。
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from shiboken6 import isValid

from src.features.battle_report.analysis.buff_evidence import BattleBuffEvidencePanel
from src.features.battle_report.editing.control import BattleBuildSnapshotControl
from src.features.battle_report.marginal.page import BattleMarginalPage
from src.features.battle_report.capture.overlay import BattleReportOverlay


_APP = QApplication.instance() or QApplication([])


def flush_delete():
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_hidden_controls_die_with_their_owner():
    for _ in range(3):
        owner = QWidget()
        controls = BattleBuildSnapshotControl(owner)
        buffs = BattleBuffEvidencePanel(owner)
        held = [controls.status, controls.import_button, controls.environment_button,
                controls.activation_button, buffs.summary_label]
        try:
            owner.show()
            assert all(widget.isHidden() for widget in held)
            controls.set_state(has_edit=True, active=True)
            buffs.clear()
            assert all(widget.isHidden() for widget in held)
        finally:
            owner.deleteLater()
            flush_delete()
        # 保留 Python 引用，确认不是依赖垃圾回收碰巧把孤儿控件清掉。
        assert all(not isValid(widget) for widget in held)


def test_independent_overlay_survives_window_hide_and_reopens(tmp_path):
    owner = QWidget()
    overlay = BattleReportOverlay(game_ui_asset_root=Path(tmp_path))
    try:
        owner.show()
        overlay.show_overlay()
        geometry = overlay.geometry()
        assert overlay.parent() is None
        owner.hide()
        assert overlay.isVisible()
        assert overlay.isWindow()
        assert overlay.windowFlags() & Qt.Tool
        for _ in range(3):
            overlay.close()
            assert isValid(overlay) and not overlay.isVisible()
            overlay.show_overlay()
            assert overlay.isVisible() and overlay.geometry() == geometry
        overlay.set_passthrough(False)
        assert overlay.isVisible() and overlay.geometry() == geometry
    finally:
        overlay.close()
        overlay.deleteLater()
        owner.deleteLater()
        flush_delete()
    assert not isValid(overlay)


def test_fork_panel_returns_from_editor_before_reset_then_dies_with_page(tmp_path):
    owner = QWidget()
    page = BattleMarginalPage(game_ui_asset_root=Path(tmp_path), parent=owner)
    panel, table = page.fork_benefit_panel, page.fork_benefit_table
    try:
        owner.show()
        assert panel.isHidden()
        for _ in range(3):
            # 使用真实 Qt 编辑器容器交接所有权；不模拟角色分析或保存成功。
            editor = QWidget(page)
            QVBoxLayout(editor).addWidget(panel)
            panel.show()
            assert not panel.isHidden()
            page.clear_candidate()
            editor.deleteLater()
            flush_delete()
            assert isValid(panel) and isValid(table) and panel.isHidden()
            page.set_editor_data({"details": []})
            assert isValid(panel) and panel.isHidden()
    finally:
        owner.deleteLater()
        flush_delete()
    assert not isValid(panel) and not isValid(table)
