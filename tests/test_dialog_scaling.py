# 测试对话框缩放与居中。
"""Regression coverage for high-DPI dialog work-area fitting."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGroupBox,
    QPushButton,
    QScrollArea,
    QWidget,
)

from src.app import theme as theme_module
from src.app.window_geometry import (
    constrained_window_size,
    fit_dialog_to_available_screen,
)
from src.app.theme import install_dialog_defaults
from src.ui import equipment_replacement_dialog as replacement_module
from src.ui.equipment_replacement_dialog import (
    EquipmentReplacementCard,
    show_equipment_replacement_dialog,
)


def test_fixed_dialog_is_relaxed_and_fitted_to_scaled_work_area() -> None:
    QApplication.instance() or QApplication([])
    dialog = QDialog()
    dialog.setFixedSize(1200, 920)
    available = QRect(100, 50, 1000, 700)

    fitted = fit_dialog_to_available_screen(
        dialog,
        QSize(1200, 920),
        available_geometry=available,
    )

    assert fitted == QSize(952, 652)
    assert dialog.size() == fitted
    assert dialog.minimumWidth() <= fitted.width()
    assert dialog.minimumHeight() <= fitted.height()
    assert available.contains(dialog.frameGeometry().center())
    actual_center = dialog.geometry().center()
    assert abs(actual_center.x() - available.center().x()) <= 1
    assert abs(actual_center.y() - available.center().y()) <= 1


def test_dialog_with_nested_parent_uses_the_top_level_global_center() -> None:
    app = QApplication.instance() or QApplication([])
    top_level = QWidget()
    top_level.setGeometry(420, 260, 600, 400)
    nested_parent = QWidget(top_level)
    nested_parent.setGeometry(150, 80, 200, 120)
    top_level.show()
    app.processEvents()
    dialog = QDialog(nested_parent)

    fit_dialog_to_available_screen(
        dialog,
        QSize(300, 160),
        available_geometry=QRect(0, 0, 1920, 1080),
    )

    expected = top_level.mapToGlobal(top_level.rect().center())
    actual = dialog.geometry().center()
    assert abs(actual.x() - expected.x()) <= 1
    assert abs(actual.y() - expected.y()) <= 1
    top_level.hide()


@pytest.mark.parametrize("scale", (1.0, 1.25, 1.5, 1.75, 2.0))
def test_dialog_geometry_covers_common_windows_scaling_ratios(scale: float) -> None:
    available = QRect(0, 0, round(1920 / scale), round(1040 / scale))
    fitted = constrained_window_size(QSize(1064, 920), available)

    assert fitted.width() <= available.width() - 48
    assert fitted.height() <= available.height() - 48
    if scale == 1.0:
        assert fitted == QSize(1064, 920)


def test_global_dialog_defaults_fit_future_oversized_dialogs() -> None:
    app = QApplication.instance() or QApplication([])
    install_dialog_defaults(app)
    available = app.primaryScreen().availableGeometry()
    dialog = QDialog()
    dialog.setFixedSize(available.width() + 200, available.height() + 200)

    dialog.show()
    app.processEvents()

    assert dialog.width() <= max(1, available.width() - 48)
    assert dialog.height() <= max(1, available.height() - 48)
    dialog.hide()


@pytest.mark.parametrize("scale", (1.0, 1.25, 1.5, 1.75, 2.0))
def test_replacement_dialog_keeps_confirm_action_inside_work_area(
    monkeypatch,
    scale: float,
) -> None:
    app = QApplication.instance() or QApplication([])
    install_dialog_defaults(app)
    available = QRect(0, 0, round(1920 / scale), round(1040 / scale))
    observed: dict[str, object] = {}

    def fit_to_test_work_area(dialog, preferred_size=None, **_kwargs):
        return fit_dialog_to_available_screen(
            dialog,
            preferred_size,
            available_geometry=available,
        )

    def inspect_dialog(dialog: QDialog) -> int:
        dialog.show()
        app.processEvents()
        confirm = next(
            button
            for button in dialog.findChildren(QPushButton)
            if button.text() == "确定替换"
        )
        observed["dialog_width"] = dialog.width()
        observed["dialog_height"] = dialog.height()
        observed["confirm_bottom"] = confirm.mapTo(
            dialog, confirm.rect().bottomLeft(),
        ).y()
        observed["candidate_minimum"] = dialog.findChild(
            QGroupBox, "equipmentReplacementCandidateGroup",
        ).minimumHeight()
        observed["comparison_minimum"] = dialog.findChild(
            QScrollArea, "equipmentReplacementComparisonScroll",
        ).minimumHeight()
        dialog.hide()
        return QDialog.Rejected

    monkeypatch.setattr(
        replacement_module,
        "fit_dialog_to_available_screen",
        fit_to_test_work_area,
    )
    monkeypatch.setattr(
        theme_module,
        "fit_dialog_to_available_screen",
        fit_to_test_work_area,
    )
    monkeypatch.setattr(QDialog, "exec", inspect_dialog)
    card = EquipmentReplacementCard(
        key="current",
        item_view={"display_name": "当前装备"},
        score=100,
        grade="A",
        direct_damage_score=None,
        payload=None,
    )
    show_equipment_replacement_dialog(
        QDialog(),
        title="替换装备",
        role_name="测试角色",
        summary="缩放适配测试",
        current=card,
        candidates=[
            EquipmentReplacementCard(
                key="candidate",
                item_view={"display_name": "候选装备"},
                score=110,
                grade="S",
                direct_damage_score=None,
                payload=None,
            )
        ],
        on_confirm=lambda _choice: None,
    )

    assert 60 <= observed["candidate_minimum"] <= 120
    assert 140 <= observed["comparison_minimum"] <= 310
    assert observed["dialog_width"] <= max(1, available.width() - 48)
    assert observed["dialog_height"] <= max(1, available.height() - 48)
    assert observed["confirm_bottom"] < observed["dialog_height"]
