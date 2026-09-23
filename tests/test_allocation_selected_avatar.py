# 验证计算角色已选头像可点击移回待选区。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_selected_role_avatar_click_returns_role_to_available_pool() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFrame, QLabel

    from src.features.allocation.role_selector import RoleSelector

    QApplication.instance() or QApplication([])
    selector = RoleSelector()
    selector.load_roles({"黑羽": {"character_id": 1042}}, [])
    selector.selected = ["黑羽"]
    selector._render_grid()

    unit = selector.priority_layout.itemAtPosition(0, 0).widget()
    card = unit.findChild(QFrame)
    avatar = card.findChild(QLabel)
    QTest.mouseClick(
        card,
        Qt.MouseButton.LeftButton,
        pos=avatar.mapTo(card, avatar.rect().center()),
    )

    assert selector.selected == []
    assert selector._available_role_names() == ["黑羽"]
