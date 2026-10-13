# 结算后只保留一份已装备物品，战报上下文通过引用关联正式冻结表。
from copy import deepcopy
import hashlib
import json


def compact_equipment_context(context, materialized_items, character_ids):
    """Only called inside new-report finalization; never rewrites saved history."""
    result = deepcopy(dict(context))
    def key(item):
        return int(item["uid_slot"]), int(item["uid_serial"])
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")).encode("utf-8")).hexdigest()
    canonical = {key(item): item for item in materialized_items or ()
                 if item.get("equipped") and item.get("equipped_character_id") in character_ids}
    # Conflicting half configurations cannot be represented by the unified build.
    # Preserve each distinct equipped value once, without becoming a candidate pool.
    scope_only, unprojected = {}, {}
    def references(items):
        refs = []
        for item in items or ():
            uid = key(item)
            ref = {"uid_slot": uid[0], "uid_serial": uid[1]}
            if canonical.get(uid) == item:
                ref["storage"] = "battle_equipment_snapshot"
            else:
                sha = digest(item)
                scope_only.setdefault(sha, deepcopy(item))
                ref.update(storage="scope_equipment", sha256=sha)
            refs.append(ref)
        return refs
    result.pop("native_runtime_snapshot", None)
    result.pop("equipment", None)
    for entry in (result.get("native_scope_builds") or {}).values():
        entry["equipment_refs"] = references(entry.pop("equipment", []))
        snapshot = entry.get("snapshot") or {}
        snapshot.pop("prior_character_observation", None)
        snapshot.pop("inventory_projection", None)
        domains = snapshot.get("domains") or {}
        domains.pop("inventory", None)
        projection = snapshot.get("character_projection") or {}
        equipped = projection.get("battleEquipment") or {}
        if "items" in equipped:
            equipped.pop("items")
            equipped["itemRefs"] = deepcopy(entry["equipment_refs"])
        for row in (domains.get("character") or {}).get("records", []):
            for field in ("equippedCassettes", "equippedDriveBlocks"):
                for attachment in row.get(field, []):
                    raw = attachment.pop("item", None)
                    if raw is not None:
                        sha = digest(raw)
                        attachment["sourceItemSha256"] = sha
                        uid = raw.get("UniqueID") or {}
                        if not any(ref["uid_slot"] == uid.get("solt") and ref["uid_serial"] == uid.get("serial")
                                   for ref in entry["equipment_refs"]):
                            unprojected.setdefault(sha, raw)
                            attachment["storage"] = "unprojected_equipment"
            if "equippedCassettes" in row or "equippedDriveBlocks" in row:
                row["equipped_items_storage"] = "scope_equipment_refs"
    result["equipment_storage"] = {"version": 1, "canonical": "battle_equipment_snapshot"}
    if scope_only:
        result["equipment_storage"]["scope_equipment"] = scope_only
    if unprojected:
        result["equipment_storage"]["unprojected_equipment"] = unprojected
    return result
