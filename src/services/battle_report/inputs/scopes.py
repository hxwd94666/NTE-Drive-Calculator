# 核对已保留首击与半场证据，选择同一队伍子集；子集不晋升为完整库存。
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from math import isfinite

from src.services.battle_report.capture.build_context import PANEL_DOMAINS


def observe_native_scopes(client, writer, operation_id, record, *, final=False, stop_requested=None):
    if not getattr(client, "native_capture", False) or writer is None:
        return
    snapshot = client.observe_battle_scopes(record, final=final, stop_requested=stop_requested)
    if snapshot is not None:
        writer.bind_runtime_snapshot(capture_operation_id=operation_id, snapshot=snapshot)


def scope_attempts(record):
    native = record.get("native_capture") or {}
    attempts = native.get("scopeAttempts")
    if not isinstance(attempts, Mapping):
        raise ValueError("采集组件缺少半场快照生命周期能力，请更新采集组件。")
    if set(attempts) - {"combat", "upper", "lower"}:
        raise ValueError("原生战报包含未知战斗范围。")
    for value in attempts.values():
        if not isinstance(value, Mapping) or not str(value.get("attemptId") or "").isdecimal():
            raise ValueError("原生战报缺少唯一的战斗尝试身份。")
    return attempts


def _team_members(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
        return None
    result = []
    for row in rows:
        if not isinstance(row, Mapping):
            return None
        slot, role, key = row.get("slotIndex"), row.get("DefaultCharacterID"), row.get("objectKey")
        if (type(slot) is not int or slot != len(result) or not isinstance(role, str)
                or not role.isdecimal() or int(role) <= 0 or not isinstance(key, str)):
            return None
        parts = key.split(":")
        if len(parts) != 2 or any(not part.isdecimal() or int(part) <= 0 for part in parts):
            return None
        result.append((slot, role, key))
    if len({row[2] for row in result}) != len(result):
        return None
    return tuple(result)


def team_configuration_unchanged(snapshot, attempt, record):
    """由已保存上下文证明队伍身份；出场角色切换不等于配装变更。"""
    native = (record or {}).get("native_capture") or {}
    team = (snapshot.get("domains") or {}).get("team") or {}
    if (native.get("droppedContexts") not in ("0", 0)
            or not native.get("providerId") or native["providerId"] != team.get("providerId")):
        return False
    rows = team.get("records") or []
    if len(rows) != 1 or not isinstance(rows[0], Mapping):
        return False
    members = _team_members(rows[0].get("EquippedPlayers"))
    selected = rows[0].get("CharacterItems")
    if (members is None or not isinstance(selected, list) or len(selected) != len(members)
            or any(not isinstance(row, Mapping) or row.get("slotIndex") != member[0]
                   or row.get("ItemID") != member[1] for row, member in zip(selected, members))):
        return False
    contexts = _scope_contexts(attempt, native)
    if contexts is None:
        return False
    baseline_roots = contexts[0].get("roots") or {}
    roots = tuple(baseline_roots.get(key) for key in ("world", "controller", "ps"))
    if any(not isinstance(key, str) or not key for key in roots):
        return False
    prefix = f"world-{roots[0]}/controller-{roots[1]}/ps-{roots[2]}/"
    if not str(team.get("domainKey") or "").startswith(prefix):
        return False
    for event in contexts:
        observed = event.get("team") or {}
        event_roots = event.get("roots") or {}
        if (tuple(event_roots.get(key) for key in ("world", "controller", "ps")) != roots
                or observed.get("source") != "PlayerState.EquippedPlayers"
                or observed.get("enumerationComplete") is not True
                or _team_members(observed.get("actors")) != members):
            return False
    return True


def _scope_contexts(attempt, native):
    """把每个保留范围变化与同时间戳来源上下文逐项匹配。"""
    try:
        first, last = float(attempt["firstUnixSeconds"]), float(attempt["lastUnixSeconds"])
        if not isfinite(first) or not isfinite(last) or not 0 < first <= last:
            return None
        events = [(int(event["observedUnixUs"]) / 1_000_000, event)
                  for event in native.get("contextEvents", []) if event.get("kind") == "combat_context"]
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None
    if any(at <= 0 or (index and at <= events[index - 1][0]) for index, (at, _) in enumerate(events)):
        return None
    before = [event for at, event in events if at <= first]
    during = [event for at, event in events if first < at <= last]
    if (not before or before[-1].get("snapshotChanges") != attempt.get("firstChanges")
            or [event.get("snapshotChanges") for event in during] != attempt.get("changes", [])):
        return None
    return [before[-1], *during]


def _pending_cache_was_read(actual, current, event, native):
    if (current.get("dirty") is not True or current.get("revision") != actual.get("revision")
            or actual.get("dirty") is not False or native.get("droppedContexts") not in (0, "0")
            or not native.get("providerId") or native["providerId"] != actual.get("providerId")):
        return False
    roots = tuple((event.get("roots") or {}).get(k) for k in ("world", "controller", "ps"))
    if any(not isinstance(k, str) or not k for k in roots):
        return False
    if not str(actual.get("domainKey") or "").startswith(f"world-{roots[0]}/controller-{roots[1]}/ps-{roots[2]}/"):
        return False
    try:
        return 0 < int(event["observedUnixUs"]) <= int(actual["observedUnixUs"])
    except (KeyError, TypeError, ValueError):
        return False


def _same_character_configuration(snapshot, attempt, record):
    """即使配装相同，入场刷新也会改变观测代次。"""
    before = snapshot.get("prior_character_observation") or {}
    after = (snapshot.get("domains") or {}).get("character") or {}
    native = (record or {}).get("native_capture") or {}
    expected = (attempt.get("firstChanges") or {}).get("character") or {}
    contexts = _scope_contexts(attempt, native)
    if (not contexts or native.get("droppedContexts") not in (0, "0")
            or not native.get("providerId") or expected.get("revision") != before.get("revision")):
        return False
    roots = contexts[0].get("roots") or {}
    prefix = f"world-{roots.get('world')}/controller-{roots.get('controller')}/ps-{roots.get('ps')}/inventory-"
    for observed in (before, after):
        if (observed.get("providerId") != native["providerId"]
                or observed.get("enumerationComplete") is not True or observed.get("dirty") is not False
                or observed.get("failed") or observed.get("truncated")
                or not str(observed.get("domainKey") or "").startswith(prefix)):
            return False
    if (before["domainKey"].split("/clone-", 1)[0] != after["domainKey"].split("/clone-", 1)[0]
            or not before.get("records") or before["records"] != after.get("records")):
        return False
    try:
        return (0 < int(before["observedUnixUs"]) <= float(attempt["firstUnixSeconds"]) * 1_000_000
                <= int(after["observedUnixUs"]))
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def validate_first_hit(snapshot, attempt, record=None):
    """后续读取仅在修订仍属于第一击时可用。"""
    changes = attempt.get("firstChanges") or {}
    refs = attempt.get("firstSnapshotRefs") or {}
    domains = snapshot.get("domains") or {}
    for domain in PANEL_DOMAINS:
        expected = changes.get(domain) or refs.get(domain) or {}
        actual = domains.get(domain) or {}
        reference = refs.get(domain) or {}
        if reference.get("providerId"):
            # 首击直接引用比缓存上下文更新；
            # 仅修订号不能证明跨 provider 或场景身份一致。
            if any(reference.get(k) != actual.get(k) for k in ("providerId", "domain", "domainKey")):
                return "first_hit_configuration_unverified"
            expected = reference
        if not expected.get("revision") or expected.get("revision") != actual.get("revision"):
            if domain == "team" and team_configuration_unchanged(snapshot, attempt, record):
                continue
            if domain == "character" and not reference and _same_character_configuration(snapshot, attempt, record):
                continue
            return "first_hit_configuration_unverified"
    return None


def scope_changed(snapshot, attempt, record=None):
    reason = validate_first_hit(snapshot, attempt, record)
    if reason:
        return reason
    same_team = team_configuration_unchanged(snapshot, attempt, record)
    native = (record or {}).get("native_capture") or {}
    contexts = _scope_contexts(attempt, native)
    for index, changes in enumerate(attempt.get("changes") or ()):
        for domain in PANEL_DOMAINS:
            current = changes.get(domain) or {}
            if current.get("dirty") is not False or current.get("revision") != snapshot["domains"][domain]["revision"]:
                if domain == "team" and same_team:
                    continue
                if contexts is not None and _pending_cache_was_read(
                        snapshot["domains"][domain], current, contexts[index + 1], native):
                    continue
                return "native_scope_configuration_changed"
    return None


def team_character_ids(snapshot):
    """保留所有已观测当前队伍成员，包括零伤害成员。"""
    result = set()
    for row in (snapshot.get("domains", {}).get("team", {}).get("records") or ()):
        for member in row.get("CharacterItems") or ():
            value = str(member.get("ItemID") or "")
            if value.isdecimal() and int(value) > 0:
                result.add(int(value))
    return result


def select_scope_builds(build, record):
    """只有 Core 归约后保留的尝试身份可生成正式计算配装。"""
    attempts = scope_attempts(record)
    saved = build.get("native_scope_builds") or {}
    result = deepcopy(build)
    result.update(snapshot_id=None, profiles={}, stat_snapshots={}, equipment=[])
    result["native_scope_builds"] = {}
    result["native_scope_validation"] = {}
    reason = None
    conflict_reason = None
    conflicted = set()
    equipment = {}
    for scope, attempt in attempts.items():
        entry = saved.get(scope) or {}
        if entry.get("attempt_id") != attempt["attemptId"]:
            reason = "native_scope_snapshot_missing"
            result["native_scope_validation"][scope] = {"state": "unavailable", "reason": reason}
            continue
        result["native_scope_builds"][scope] = entry
        snapshot = entry["snapshot"]
        scope_reason = entry.get("calculation_unavailable_reason") or (
            validate_first_hit(snapshot, attempt, record) if snapshot.get("retention") == "dll_pinned"
            else scope_changed(snapshot, attempt, record))
        ids = team_character_ids(snapshot)
        if not scope_reason and (not ids or ids != {int(key) for key in entry["profiles"]}):
            scope_reason = "native_scope_team_profiles_missing"
        result["native_scope_validation"][scope] = {
            "state": "unavailable" if scope_reason else "ready", "reason": scope_reason,
        }
        if scope_reason:
            reason = scope_reason
            if scope_reason != "native_scope_team_profiles_missing":
                continue
        for key, profile in entry["profiles"].items():
            cid = int(key)
            if cid in result["profiles"] and result["profiles"][cid] != profile:
                conflict_reason = "native_cross_half_character_configuration_conflict"
                conflicted.add(cid)
            result["profiles"][cid] = profile
            result["stat_snapshots"][cid] = entry["stat_snapshots"].get(str(cid), entry["stat_snapshots"].get(cid, []))
        for item in entry["equipment"]:
            uid = (item["uid_serial"], item["uid_slot"])
            if uid in equipment and equipment[uid] != item:
                conflict_reason = "native_cross_half_equipment_configuration_conflict"
                conflicted.update((item.get("equipped_character_id"), equipment[uid].get("equipped_character_id")))
            equipment[uid] = item
    result["native_conflict_character_ids"] = sorted(cid for cid in conflicted if cid is not None)
    result["profiles"] = {cid: row for cid, row in result["profiles"].items() if cid not in conflicted}
    result["stat_snapshots"] = {cid: rows for cid, rows in result["stat_snapshots"].items() if cid not in conflicted}
    result["equipment"] = [item for item in equipment.values() if item.get("equipped_character_id") not in conflicted]
    return result, conflict_reason or reason


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
    equipped = profiles.get("battleEquipment") or {}
    for field, owner in (("items", "equipped_character_id"), ("characters", "character_id")):
        if field in equipped:
            equipped[field] = [row for row in equipped[field] if row.get(owner) in ids]
    domains = selected.get("domains") or {}
    strings = {str(value) for value in ids}
    # 入场刷新前后使用同队伍子集比较；保留原观测身份，
    # 不把子集当成当前正式快照。
    previous = selected.get("prior_character_observation")
    if previous is not None:
        previous["records"] = [row for row in previous.get("records", []) if str(row.get("ItemID")) in strings]
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
    # 元数据仍表示来源的身份、数量和覆盖范围；
    # 明确标记 selection，不伪造新完整背包或 provider RPC 页。
    selected["selection"] = {"kind": "team_subset", "character_ids": sorted(ids)}
    return selected
