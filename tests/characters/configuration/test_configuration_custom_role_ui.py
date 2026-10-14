# 验证自建底盘编辑在达到二十格后保留原草稿，不产生不可提交的第廿一格。

from __future__ import annotations

import os
from types import SimpleNamespace

NTE_TEST_TIER = "core"


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

def test_custom_role_board_prevents_a_twenty_first_enabled_cell() -> None:
    from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

    from src.features.configuration.editing.form import _add_custom_board

    QApplication.instance() or QApplication([])
    container = QWidget()
    layout = QVBoxLayout(container)
    role_data = {
        "character_id": 1_500_000_000,
        "board_cells": [
            {
                "row": row,
                "column": column,
                "is_enabled": row <= 4,
                "is_locked": False,
            }
            for row in range(1, 6)
            for column in range(1, 6)
        ],
    }
    window = SimpleNamespace(_config_dirty_board_ids=set(), _config_dirty=False)

    _add_custom_board(window, "自建角色", role_data, layout)

    by_position = {
        (int(button.property("boardRow")), int(button.property("boardColumn"))): button
        for button in container.findChildren(QPushButton)
    }
    by_position[(5, 1)].click()

    assert sum(cell["is_enabled"] for cell in role_data["board_cells"]) == 20
    assert not role_data["board_cells"][20]["is_enabled"]
    assert not window._config_dirty
    count_label = container.findChild(QWidget, "customRoleBoardEnabledCount")
    assert count_label is not None
    assert count_label.text() == "已启用 20/20"


