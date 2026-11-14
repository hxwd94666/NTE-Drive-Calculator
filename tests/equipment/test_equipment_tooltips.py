# 验证配装页面空提示被过滤，毕业率、权重及页面外有效提示保持可用。
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from src.ui.common.text_catalog import plain_tooltip
from src.ui.common.tooltip_policy import suppress_empty_tooltips, tooltip_has_information
from src.features.inventory.equipment.optimization.graduation_controller import request_equipment_graduation_rate
from src.ui.equipment.attribute_summary_panel import AttributeSummaryPanel, AttributeSummaryRow
from src.ui.equipment.equipment_presentation import EquipmentPresentation
from src.ui.equipment.puzzle_board import PuzzleBoardWidget


class HelpTarget(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.delivered = 0

    def event(self, event):
        if event.type() == QEvent.ToolTip:
            self.delivered += 1
            event.accept()
            return True
        return super().event(event)


@pytest.fixture
def equipment_page():
    app = QApplication.instance() or QApplication([])
    page, outside = QWidget(), QWidget()
    suppress_empty_tooltips(page)
    yield app, page, outside
    for owner in (page, outside):
        owner.close()
        owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def hover(app, target):
    event = QHelpEvent(QEvent.ToolTip, QPoint(1, 1), QPoint(1, 1))
    app.sendEvent(target, event)


@pytest.mark.parametrize("text", ["", " ", "\n\t", "\u00a0"])
def test_empty_plain_tooltip_is_removed_not_wrapped(text):
    assert plain_tooltip(text) == ""


@pytest.mark.parametrize("text", ["", "  ", "<qt></qt>", "<qt><br>&nbsp;</qt>", "<html><body><p> </p></body></html>"])
def test_empty_help_is_suppressed_for_late_created_equipment_children(equipment_page, text):
    app, page, _outside = equipment_page
    target = HelpTarget(page)
    target.setToolTip(text)
    hover(app, target)
    assert target.delivered == 0


def test_graduation_and_weight_help_remain_available(equipment_page):
    app, page, _outside = equipment_page
    graduation = HelpTarget(page)
    state = {"_graduation_rate_loaded": True, "_graduation_rate": 66.4, "_graduation_tooltip": "毕业率：按同一毕业基准比较"}
    request_equipment_graduation_rate(object(), "角色", state, graduation, database_path="unused")
    weights = HelpTarget(page)
    weights.setToolTip(plain_tooltip("词条权重：1.25"))
    for target in (graduation, weights):
        before = target.toolTip()
        hover(app, target)
        assert target.delivered == 1 and target.toolTip() == before
    assert graduation.text() == "66.4%"


def test_inherited_help_and_other_pages_are_not_disabled(equipment_page):
    app, page, outside = equipment_page
    container = QWidget(page)
    container.setToolTip("有效的父容器提示")
    inherited = HelpTarget(container)
    unrelated = HelpTarget(outside)
    hover(app, inherited)
    hover(app, unrelated)
    assert inherited.delivered == 1 and unrelated.delivered == 1
    container.setToolTip("<qt><br></qt>")
    hover(app, inherited)
    assert inherited.delivered == 1


@pytest.mark.parametrize("text", ["5% < x < 10%", "<unknown>", "<qt>&lt;tag&gt; &amp; 权重</qt>", "<qt><img src='example'/></qt>"])
def test_plain_symbols_rich_content_and_image_help_are_not_mistaken_for_empty(text):
    assert tooltip_has_information(text)


def test_real_equipment_page_covers_board_summary_and_keeps_rendered_stat_weight(tmp_path, monkeypatch):
    from src.features.inventory.equipment.display import display_view

    app = QApplication.instance() or QApplication([])
    host = SimpleNamespace(equip_saved_mode_btn=object(), _batch_delete_equipment=lambda: None,
                           _preview_fast_assemble_all_roles=lambda: None,
                           _preview_automatic_assemble_all_roles=lambda: None)
    # 只隔离账号读取与主从列表，页面入口、图纸、属性和装备词条均使用真实控件。
    monkeypatch.setattr(display_view, "build_equipment_master_detail", lambda _host, _layout: None)
    page = display_view._page_equipment(host)
    try:
        board = PuzzleBoardWidget([["H_3", "H_3", "H_3"]], parent=page)
        summary = AttributeSummaryPanel("角色", {"equipment": (AttributeSummaryRow("crit", "暴击率", 24.4, True),)}, parent=page)
        for container in (board, summary):
            target = HelpTarget(container)
            hover(app, target)
            assert target.delivered == 0
        presentation = EquipmentPresentation(
            app_context=SimpleNamespace(paths=SimpleNamespace(game_ui_asset_root=tmp_path)), dialog_parent=page)
        card = presentation._equip_card("测试卡带", "攻击力%", {"暴击率%": 5}, None, "test", {"暴击率%": 1.25})
        card.setParent(page)
        weight = next(label for label in card.findChildren(QLabel) if "1.25" in label.toolTip())
        target = HelpTarget(weight)
        hover(app, target)
        assert target.delivered == 1 and "1.25" in weight.toolTip()
    finally:
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
