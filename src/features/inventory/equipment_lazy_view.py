# 提供配装装备列表的延迟加载视图。
"""Legacy scroll-anchor helpers retained for compatibility tests."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QTimer

from src.features.inventory.equipment_plan_renderer import _render_equip_role


EQUIPMENT_ROLE_PLACEHOLDER_HEIGHT = 520
EQUIPMENT_VIEWPORT_PREFETCH_COUNT = 1


def capture_equipment_restore_anchor(window: Any, preferred_role_name=None):
    scroll = getattr(window, "equip_scroll", None)
    if scroll is None:
        return None
    bar = scroll.verticalScrollBar()
    entries = list(getattr(window, "_equip_lazy_entries", []) or [])
    target = next(
        (entry for entry in entries if entry.get("role_name") == preferred_role_name),
        None,
    )
    if target is None:
        viewport_top = bar.value()
        viewport_bottom = viewport_top + max(1, scroll.viewport().height())
        target = next(
            (
                entry
                for entry in entries
                if (slot := entry.get("slot")) is not None
                and slot.y() + slot.height() > viewport_top
                and slot.y() < viewport_bottom
            ),
            None,
        )
    anchor = {
        "role_name": target.get("role_name") if target else preferred_role_name,
        "viewport_offset": None,
        "scroll_value": bar.value(),
        "load_token": None,
        "render_token": None,
        "attempts": 0,
        "scheduled": False,
    }
    if target is not None and target.get("slot") is not None:
        anchor["viewport_offset"] = scroll.viewport().mapFromGlobal(
            target["slot"].mapToGlobal(QPoint(0, 0))
        ).y()
    return anchor


def schedule_equipment_restore_anchor(window: Any, token=None) -> None:
    anchor = getattr(window, "_equip_restore_anchor", None)
    if not isinstance(anchor, dict) or anchor.get("scheduled"):
        return
    render_token = anchor.get("render_token")
    if render_token is None or (token is not None and token is not render_token):
        return
    if render_token is not getattr(window, "_equip_render_token", None):
        return
    anchor["scheduled"] = True
    QTimer.singleShot(
        50,
        lambda current=render_token: restore_equipment_anchor(window, current),
    )


def restore_equipment_anchor(window: Any, token) -> None:
    anchor = getattr(window, "_equip_restore_anchor", None)
    if not isinstance(anchor, dict) or anchor.get("render_token") is not token:
        return
    anchor["scheduled"] = False
    if token is not getattr(window, "_equip_render_token", None):
        return
    scroll = getattr(window, "equip_scroll", None)
    if scroll is None:
        window._equip_restore_anchor = None
        return
    entry = next(
        (
            item
            for item in getattr(window, "_equip_lazy_entries", [])
            if item.get("role_name") == anchor.get("role_name")
        ),
        None,
    )
    if entry is not None and not entry.get("loaded"):
        render_lazy_equipment_entry(window, entry)
    if entry is not None and entry.get("slot") is not None:
        slot_top = entry["slot"].mapTo(window.equip_content, QPoint(0, 0)).y()
        offset = anchor.get("viewport_offset")
        desired = slot_top - int(offset) if offset is not None else slot_top
        scroll.verticalScrollBar().setValue(max(0, desired))
    else:
        scroll.verticalScrollBar().setValue(int(anchor.get("scroll_value") or 0))
    anchor["attempts"] = int(anchor.get("attempts") or 0) + 1
    if anchor["attempts"] < 8:
        schedule_equipment_restore_anchor(window, token)
    else:
        window._equip_restore_anchor = None


def clear_layout_widgets(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()


def render_lazy_equipment_entry(window: Any, entry) -> None:
    layout = entry["layout"]
    clear_layout_widgets(layout)
    group = _render_equip_role(
        window,
        entry["role_name"],
        entry["state"],
        target_layout=layout,
    )
    entry["loaded"] = True
    entry["slot"].setFixedHeight(
        max(EQUIPMENT_ROLE_PLACEHOLDER_HEIGHT, group.sizeHint().height() + 8)
    )
    schedule_equipment_restore_anchor(window)
