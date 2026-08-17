# 测试分配筛选设置界面。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_filter_dialog_defaults_to_no_selected_options() -> None:
    from PySide6.QtWidgets import QApplication

    from src.features.allocation.filter_settings_dialog import (
        AllocationFilterSettingsDialog,
    )

    QApplication.instance() or QApplication([])
    dialog = AllocationFilterSettingsDialog()

    assert dialog.windowTitle() == "分配设置"
    assert not any(button.isChecked() for button in dialog.quality_buttons.values())
    assert not any(button.isChecked() for button in dialog.type_buttons.values())


def test_filter_dialog_uses_independent_multi_select_buttons() -> None:
    from PySide6.QtWidgets import QApplication

    from src.features.allocation.filter_settings_dialog import (
        AllocationFilterSettingsDialog,
    )

    QApplication.instance() or QApplication([])
    dialog = AllocationFilterSettingsDialog()

    dialog.quality_buttons["Blue"].click()
    dialog.quality_buttons["Gold"].click()
    dialog.type_buttons["tape"].click()

    assert dialog.settings().qualities == frozenset({"Blue", "Gold"})
    assert dialog.settings().item_types == frozenset({"tape"})


def test_filter_dialog_reports_type_without_quality(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from src.features.allocation.filter_settings_dialog import (
        AllocationFilterSettingsDialog,
    )

    QApplication.instance() or QApplication([])
    dialog = AllocationFilterSettingsDialog()
    dialog.type_buttons["drive"].click()
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    dialog._accept_valid_settings()

    assert warnings == [
        ("分配设置无效", "选择分配类型后，必须至少选择一种分配品质。")
    ]
    assert dialog.result() == 0


def test_filter_dialog_places_type_before_quality_and_moves_description_to_help(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication, QGroupBox, QPushButton

    from src.features.allocation import filter_settings_dialog
    from src.features.allocation.filter_settings_dialog import AllocationFilterSettingsDialog

    QApplication.instance() or QApplication([])
    dialog = AllocationFilterSettingsDialog()
    module = dialog.findChild(QGroupBox, "allocationFilterModule")
    assert module is not None
    assert module.title() == "筛选设置"
    first_selection_row = module.layout().itemAt(0).layout()
    second_selection_row = module.layout().itemAt(1).layout()
    assert first_selection_row.itemAt(0).widget().text() == "分配类型"
    assert second_selection_row.itemAt(0).widget().text() == "分配品质"

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        filter_settings_dialog,
        "show_help",
        lambda _parent, title, text: calls.append((title, text)),
    )
    help_button = dialog.findChild(QPushButton, "allocationFilterHelp")
    assert help_button is not None
    help_button.click()
    assert calls == [
        (
            "筛选设置说明",
            "对已选类型：仅已选品质会进入角色管理筛选，其他品质会被过滤；未选类型按默认规则处理。",
        )
    ]


def test_strategy_card_places_blue_settings_action_in_title_row() -> None:
    from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

    from src.features.allocation.execute_page import _build_strategy_card

    class Window:
        def _card(self, title):
            card = QFrame()
            card.setLayout(QVBoxLayout())
            label = QLabel(title)
            label.setObjectName("cardTitle")
            card.layout().addWidget(label)
            return card

        def _open_allocation_filter_settings(self):
            pass

    QApplication.instance() or QApplication([])
    host = QWidget()
    layout = QVBoxLayout(host)
    window = Window()

    _build_strategy_card(window, layout)

    assert window.allocation_filter_settings_button.objectName() == "allocationFilterSettingsButton"
    assert window.allocation_filter_settings_button.text() == "设置"
    assert window.allocation_filter_settings_button.size().width() == 54
    assert window.allocation_filter_settings_button.size().height() == 28
    header = layout.itemAt(0).widget().layout().itemAt(0).layout()
    assert header.itemAt(1).spacerItem() is not None
    assert header.itemAt(2).widget() is window.allocation_filter_settings_button
