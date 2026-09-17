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


