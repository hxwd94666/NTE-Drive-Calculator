# 从已核验完整观测派生队伍证据子集，原始页只由采集诊断归档保存。
from copy import deepcopy

from src.services.native_battle_scopes import team_character_ids


def select_native_team_snapshot(snapshot):
    selected = deepcopy(snapshot)
    if not snapshot.get("domains"):
        return selected
    ids = team_character_ids(snapshot)
    inventory = selected.get("inventory_projection") or {}
    inventory["items"] = [row for row in inventory.get("items", []) if row.get("equipped_character_id") in ids]
    if "characters" in inventory:
        inventory["characters"] = [row for row in inventory["characters"] if row.get("character_id") in ids]
    uids = {(row.get("uid", {}).get("slot"), row.get("uid", {}).get("serial")) for row in inventory["items"]}
    if "referencedItemUids" in inventory:
        inventory["referencedItemUids"] = [uid for uid in inventory["referencedItemUids"] if (uid.get("slot"), uid.get("serial")) in uids]
    profiles = selected.get("character_projection") or {}
    if "profiles" in profiles:
        profiles["profiles"] = [row for row in profiles["profiles"] if row.get("character_id") in ids]
    domains = selected.get("domains") or {}
    strings = {str(value) for value in ids}
    unknown_character = False
    for domain in ("character", "inventory"):
        source = domains.get(domain)
        if source is None:
            continue
        records = source.get("records", [])
        if domain == "character":
            unknown_character = any(not str(row.get("ItemID") or "").isdecimal() for row in records)
            source["records"] = [row for row in records if str(row.get("ItemID")) in strings]
        else:
            source["records"] = [row for row in records if
                (row.get("UniqueID", {}).get("solt"), row.get("UniqueID", {}).get("serial")) in uids]
    if unknown_character:
        selected["state"] = "unavailable"
        selected.setdefault("missing", []).append("team_character_source_identity_missing")
    # Metadata remains the source observation's identity/count/coverage. This is
    # explicitly a selection, not a new complete inventory or a provider RPC page.
    selected["selection"] = {"kind": "team_subset", "character_ids": sorted(ids)}
    return selected
