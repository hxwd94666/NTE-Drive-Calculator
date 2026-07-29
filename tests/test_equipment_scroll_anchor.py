# 验证已保存配装页的懒加载角色锚点恢复。
"""Regression coverage for the saved-plan page's lazy-render scroll anchor."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from src.features.inventory.equipment_display_view import (
    _capture_equipment_restore_anchor,
    _restore_equipment_anchor,
)


def test_equipment_restore_anchor_tracks_role_when_card_heights_change():
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(360, 260)
    host.equip_scroll = QScrollArea(host)
    host.equip_scroll.setWidgetResizable(True)
    host.equip_scroll.resize(340, 230)
    host.equip_content = QWidget()
    host.equip_content_layout = QVBoxLayout(host.equip_content)
    host.equip_scroll.setWidget(host.equip_content)

    entries = []
    for index in range(5):
        slot = QWidget(host.equip_content)
        slot.setFixedHeight(180)
        host.equip_content_layout.addWidget(slot)
        entries.append({
            "role_name": f"角色{index}", "slot": slot, "loaded": True,
        })
    host._equip_lazy_entries = entries
    host.show()
    host.equip_scroll.show()
    app.processEvents()

    target = entries[2]["slot"]
    host.equip_scroll.verticalScrollBar().setValue(target.y() - 35)
    anchor = _capture_equipment_restore_anchor(host, "角色2")
    assert anchor["role_name"] == "角色2"
    assert anchor["viewport_offset"] is not None

    # Simulate the preceding lazy card expanding after the data refresh.
    entries[0]["slot"].setFixedHeight(320)
    host.equip_content_layout.activate()
    app.processEvents()
    token = object()
    host._equip_render_token = token
    anchor["render_token"] = token
    host._equip_restore_anchor = anchor
    host.equip_scroll.verticalScrollBar().setValue(0)

    _restore_equipment_anchor(host, token)

    expected = target.y() - anchor["viewport_offset"]
    assert host.equip_scroll.verticalScrollBar().value() == expected
    host.close()
