# 验证类型已选但品质缺失时，分配筛选草稿保持未提交。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


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

    assert len(warnings) == 1
    assert dialog.result() == 0
    dialog.close()


