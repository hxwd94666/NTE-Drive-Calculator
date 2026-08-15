# 定义跨配装槽位共享装备的快照 UID 身份规则。
"""Identity rules for equipment shared by saved loadout slots."""

from __future__ import annotations

from .inventory_source_capabilities import has_native_inventory_uids


def source_snapshots_share_equipment_uids(
    left_snapshot_id: int | None,
    left_source: object,
    right_snapshot_id: int | None,
    right_source: object,
) -> bool:
    """Return whether equal persisted UID fields identify the same equipment.

    nte-core UIDs remain stable across captures.  Visual scan UIDs are only
    session-local, so two visual snapshots may legitimately contain the same
    ``(slot, serial)`` for different equipment and must never displace each
    other's saved loadouts.
    """

    if left_snapshot_id is not None and int(left_snapshot_id) == int(right_snapshot_id or 0):
        return True
    return has_native_inventory_uids(left_source) and has_native_inventory_uids(right_source)
