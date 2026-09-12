# 核对战报入场原生观测与场中变化，未知临时配装不套用账号装备。
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


NATIVE_BUILD_WARNING = "本场实际配装或场中配置连续性未确认，计算配装缺失；实测战报和入场原始观测已保留。"
DOMAINS = ("character", "inventory", "team", "environment")


def native_build_unavailable_reason(snapshot: Mapping[str, Any], record: Mapping[str, Any] | None = None) -> str | None:
    if snapshot.get("state") != "observed":
        return "native_capture_start_unavailable"
    domains = snapshot.get("domains")
    if not isinstance(domains, Mapping):
        return "native_capture_domains_missing"
    for domain in DOMAINS:
        data = domains.get(domain)
        if not isinstance(data, Mapping) or data.get("dirty") is not False or not isinstance(data.get("revision"), str):
            return "native_capture_domain_unverified"
    for row in domains["character"].get("records") or ():
        if not isinstance(row, Mapping):
            return "native_character_record_invalid"
        if (row.get("bIsTemporary") is True
                or row.get("source") == "TrialCharacterItems" or row.get("containerType") == 24):
            return "temporary_character_projection_unavailable"
        if type(row.get("bIsTemporary")) is not bool or type(row.get("bUnSaved")) is not bool:
            return "native_character_scope_unverified"
    native = (record or {}).get("native_capture") or {}
    if isinstance(native, Mapping):
        for event in native.get("contextEvents") or ():
            if not isinstance(event, Mapping) or event.get("kind") != "combat_context":
                continue
            changes = event.get("snapshotChanges")
            if not isinstance(changes, Mapping):
                return "native_context_change_coverage_missing"
            for domain in DOMAINS:
                change = changes.get(domain)
                if (not isinstance(change, Mapping) or change.get("dirty") is not False
                        or change.get("revision") != domains[domain].get("revision")):
                    return "native_context_changed_during_capture"
    return None


def native_equipment_projection(snapshot: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    projection = snapshot.get("inventory_projection")
    if not isinstance(projection, Mapping):
        return None
    if (projection.get("projectionComplete") is not True or projection.get("collectionComplete") is not True
            or projection.get("characterRefsComplete") is not True or projection.get("collectionScope") != "EQUIP"):
        return None
    raw_domain = (snapshot.get("domains") or {}).get("inventory") or {}
    if any(projection.get(key) != raw_domain.get(key) for key in ("providerId", "domainKey", "revision")):
        return None
    rows = projection.get("items")
    if not isinstance(rows, list):
        return None
    result = []
    for source in rows:
        if not isinstance(source, Mapping) or not isinstance(source.get("uid"), Mapping):
            return None
        row = deepcopy(dict(source))
        uid = row["uid"]
        if any(type(uid.get(key)) is not int or uid[key] <= 0 for key in ("slot", "serial")):
            return None
        row.update(uid_slot=uid["slot"], uid_serial=uid["serial"], grid_count=row.get("grid_count", row.get("grid")))
        result.append(row)
    return result


def native_profile_observations(snapshot: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    projection = snapshot.get("character_projection")
    if not isinstance(projection, Mapping):
        return {}
    raw_domain = (snapshot.get("domains") or {}).get("character") or {}
    if any(projection.get(key) != raw_domain.get(key) for key in ("providerId", "domainKey", "revision")):
        return {}
    return {
        int(row["character_id"]): dict(row)
        for row in projection.get("profiles") or ()
        if isinstance(row, Mapping) and type(row.get("character_id")) is int and row["character_id"] > 0
    }
