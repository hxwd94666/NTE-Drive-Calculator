# 验证公共角色选人卡的数字完整展示、槽位刷新与提示语义，不读取真实方案或执行装配。
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from src.app.theme import apply_app_theme, current_theme_name
from src.features.inventory.equipment.management.batch_selection_dialog import BatchChoice, BatchSelectionDialog
from tests.support.qt_cleanup import dispose_windows_created_by_test as dispose_windows_created_by_test


@pytest.fixture
def score_dialog():
    app = QApplication.instance() or QApplication([])
    previous_theme = current_theme_name()
    dialog = BatchSelectionDialog(
        None, [BatchChoice(1, "角色", options=((11, "低分方案"), (12, "高分方案"), (13, "无评分方案")),
                           default_id=11, option_scores=((11, 0), (12, 290.89), (13, None)))],
        title="评分展示专项", description="", accept_text="确定",
    )
    dialog.show()
    app.processEvents()
    yield app, dialog, dialog._cards[0]
    dialog.close()
    dialog.deleteLater()
    app.processEvents()
    apply_app_theme(app, previous_theme)


@pytest.mark.parametrize("theme", ["black"])
def test_numeric_score_and_tooltip_are_complete_during_slot_change(score_dialog, theme):
    app, dialog, card = score_dialog
    apply_app_theme(app, theme)
    app.processEvents()
    card.menu.actions()[1].trigger()
    # 槽位回调会立即用显示文本构造提示，不能等下一次布局才恢复被省略的数字。
    assert card.score_label.text() == "290.89"
    assert "290.89" in card.button.toolTip()
    assert "290.89" in card.score_label.toolTip()
    app.processEvents()
    assert card.score_label.text() == "290.89"
    assert card.score_label.width() >= card.score_label.fontMetrics().horizontalAdvance("290.89")
    assert card.grade_label.text() == "ACE"
    assert dialog.selected_ids() == []
    card.menu.actions()[2].trigger()
    app.processEvents()
    assert card.button.property("roleScore") is None
    assert not card.grade_label.isVisible()
    card.menu.actions()[1].trigger()
    assert card.score_label.text() == "290.89"


def test_numeric_score_survives_relayout_and_selection(score_dialog):
    app, dialog, card = score_dialog
    card.menu.actions()[1].trigger()
    card.button.click()
    for width in (640, 1000, 740):
        dialog.resize(width, 600)
        dialog.search.setText("角色")
        app.processEvents()
        assert card.score_label.text() == "290.89"
        assert card.score_label.geometry().right() < card.score_label.parentWidget().width()
        assert card.grade_label.geometry().right() < card.grade_label.parentWidget().width()
        assert dialog.selected_ids() == [12]


def test_common_card_preserves_fractional_and_zero_scores(score_dialog):
    app, _dialog, card = score_dialog
    for score, expected in ((302.08, "302.08"), (291.63, "291.63"), (286.02, "286.02"),
                            (270.3, "270.3"), (0, "0")):
        card.set_score(score)
        assert card.score_label.text() == expected
        app.processEvents()
        assert card.score_label.text() == expected
        assert card.score_label.width() >= card.score_label.fontMetrics().horizontalAdvance(expected)
