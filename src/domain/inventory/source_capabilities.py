# 统一判断库存快照来源具备的能力。
"""Inventory snapshot source capability helpers."""

from __future__ import annotations


NATIVE_INVENTORY_SOURCES = frozenset({"nte_core"})
VISUAL_INVENTORY_SOURCES = frozenset({"vision", "gamepad"})
CURRENT_INVENTORY_SOURCES = NATIVE_INVENTORY_SOURCES | VISUAL_INVENTORY_SOURCES


def is_visual_inventory_source(source: object) -> bool:
    """Return true for unified vision snapshots and legacy gamepad rows."""

    return str(source or "").strip() in VISUAL_INVENTORY_SOURCES


def has_native_inventory_uids(source: object) -> bool:
    return str(source or "").strip() in NATIVE_INVENTORY_SOURCES
