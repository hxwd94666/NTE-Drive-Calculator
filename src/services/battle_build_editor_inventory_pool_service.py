# 从打开战报编辑页时的当前稳定背包构造会话内临时候选装备池。
"""Current stable inventory candidates for battle-build editors."""

from __future__ import annotations

from typing import Any


def current_inventory_replacement_items(
    user_dao: Any,
    *,
    equipment_editable: bool,
) -> tuple[dict[str, Any], ...]:
    """Resolve the current snapshot once and freeze its items for this load."""

    if not equipment_editable:
        return ()
    snapshot_id = user_dao.current_inventory_snapshot_id()
    if snapshot_id is None:
        return ()
    locked_uids = {
        (int(row["uid_slot"]), int(row["uid_serial"]))
        for row in user_dao.list_allocation_locked_equipment_owners()
    }
    rows = []
    for source in user_dao.list_inventory_items(int(snapshot_id)):
        item = dict(source)
        uid = (int(item["uid_slot"]), int(item["uid_serial"]))
        item["allocation_reserved"] = uid in locked_uids
        rows.append(item)
    return tuple(rows)


__all__ = ["current_inventory_replacement_items"]
