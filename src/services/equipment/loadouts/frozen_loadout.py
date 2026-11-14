# 从方案自己的来源快照解析装备，供角色详情与队伍简报共享，不追随当前库存。
from src.domain.equipment.loadouts.virtual_equipment import is_virtual_equipment_assignment, virtual_equipment_inventory_item


def resolve_plan_inventory_items(user_dao, plan, *, snapshot_items=None):
    if not plan or plan.get("source_snapshot_id") is None:
        return []
    items = ([dict(item) for item in snapshot_items] if snapshot_items is not None
             else user_dao.list_inventory_items(int(plan["source_snapshot_id"])))
    by_uid = {(int(item["uid_serial"]), int(item["uid_slot"])): item for item in items}
    resolved = []
    for assignment in plan.get("assignments") or ():
        raw = dict(assignment.get("raw_assignment") or assignment)
        raw.update({key: assignment.get(key) for key in ("uid_serial", "uid_slot", "kind", "target_row", "target_column")})
        item = (virtual_equipment_inventory_item(raw) if is_virtual_equipment_assignment(raw)
                else by_uid.get((int(assignment["uid_serial"]), int(assignment["uid_slot"]))))
        if item is not None and item.get("kind") == assignment["kind"]:
            row = dict(item)
            row.update(equipped=False, equipped_character_id=None, equipped_character_name="",
                       target_row=assignment.get("target_row"), target_column=assignment.get("target_column"))
            row.pop("equipped_character_icon_path", None)
            resolved.append(row)
    return resolved
