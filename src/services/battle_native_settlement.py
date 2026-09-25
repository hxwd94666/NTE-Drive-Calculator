# 在结算时补齐原生战报计算输入，保留首击原始证据和逐角色兜底来源。
from copy import deepcopy

from src.services.native_battle_scopes import select_scope_builds, team_character_ids, validate_first_hit
from src.storage.sqlite.user_data_dao import UserDataError


def complete_native_build(service, dao, original, record, observed_ids, operation_id):
    """Database values fill missing calculation inputs, never raw DLL observations."""
    if original.get("freeze_phase") == "settlement_native":
        return original
    scopes = (record.get("native_capture") or {}).get("scopeAttempts")
    if isinstance(scopes, dict):
        selected, reason = select_scope_builds(original, record)
    else:
        selected = {**deepcopy(original), "profiles": {}, "stat_snapshots": {}, "equipment": [],
                    "snapshot_id": None, "native_scope_validation": {}}
        reason = "native_first_hit_snapshot_missing"
    ids = set(observed_ids)
    for scope, entry in (original.get("native_scope_builds") or {}).items():
        if entry.get("attempt_id") == (scopes or {}).get(scope, {}).get("attemptId"):
            ids.update(team_character_ids(entry["snapshot"]))
    profiles = {int(key): deepcopy(value) for key, value in selected["profiles"].items()}
    stats = {int(key): deepcopy(value) for key, value in selected["stat_snapshots"].items()}
    equipment = deepcopy(selected["equipment"])
    native_equipment_ids = set()
    native_world_bonuses = {}
    conflicted = set(selected.get("native_conflict_character_ids") or ())
    for scope, entry in (selected.get("native_scope_builds") or {}).items():
        validation = selected["native_scope_validation"][scope]
        if validation["state"] == "ready" or validation["reason"] == "native_scope_team_profiles_missing":
            native_equipment_ids.update(team_character_ids(entry["snapshot"]))
        elif (validation["reason"] == "native_equipment_projection_unavailable"
              and validate_first_hit(entry["snapshot"], (scopes or {})[scope], record) is None):
            # A missing equipment page does not invalidate observed role growth.
            for key, value in entry["profiles"].items():
                cid = int(key)
                if cid in profiles and profiles[cid] != value:
                    conflicted.add(cid)
                    reason = "native_cross_half_character_configuration_conflict"
                else:
                    profiles[cid] = deepcopy(value)
        if validate_first_hit(entry["snapshot"], (scopes or {})[scope], record) is None:
            native_world_bonuses.update({int(cid): entry["world_bonus"] for cid in entry["profiles"]})
    profiles = {cid: value for cid, value in profiles.items() if cid not in conflicted}
    stats = {cid: value for cid, value in stats.items() if cid not in conflicted}
    equipment = [row for row in equipment if row.get("equipped_character_id") not in conflicted]
    missing_profiles = ids - profiles.keys() - conflicted
    missing_equipment = ids - native_equipment_ids - conflicted
    missing = missing_profiles | missing_equipment
    fallback_evidence = {}
    if missing:
        fallback = service._freeze_capture_build(dao, tuple(sorted(missing)), resolve_stats=False)
        if fallback["dataset_id"] != original["dataset_id"]:
            raise UserDataError("战报静态数据集已经变化")
        used_uids = {(row["uid_serial"], row["uid_slot"]) for row in equipment}
        for cid, profile in fallback["profiles"].items():
            cid = int(cid)
            if cid in missing_profiles:
                profiles[cid] = deepcopy(profile)
            current = profiles[cid]
            if cid in missing_equipment:
                current["capture_equipment_source"] = "account_settlement_fallback"
                stats.pop(cid, None)
                if "equipment_assumption" in profile:
                    current["equipment_assumption"] = deepcopy(profile["equipment_assumption"])
            else:
                current.pop("equipment_assumption", None)
                current["capture_equipment_source"] = "native_first_hit_projection"
            current["capture_fallback_reason"] = reason or "native_character_snapshot_missing"
            fallback_evidence[str(cid)] = {
                "source": "account_settlement_fallback", "source_snapshot_id": fallback["snapshot_id"],
                "fields": (["profile"] if cid in missing_profiles else []) + (["equipment"] if cid in missing_equipment else []),
                "equipment_source": current["capture_equipment_source"], "reason": current["capture_fallback_reason"],
            }
        for row in fallback["equipment"]:
            if row.get("equipped_character_id") not in missing_equipment:
                continue
            uid = (row["uid_serial"], row["uid_slot"])
            if uid in used_uids:
                fallback_evidence[str(row["equipped_character_id"])]["equipment_conflict"] = True
                continue
            equipment.append(deepcopy(row))
            used_uids.add(uid)
    # A present profile is not proof that its resolved panel was computed.
    # Resolve independently so one malformed role cannot erase the other half.
    unresolved = []
    for cid, profile in profiles.items():
        if stats.get(cid) and any(row.get("source_group") == "resolved" for row in stats[cid]):
            continue
        try:
            stats.update(service._resolve_character_stat_snapshots(
                dependencies=service._dependencies, user_dao=dao, snapshot_id=None,
                character_ids=(cid,), profiles={cid: profile}, frozen_equipment=equipment,
                frozen_world_bonus=native_world_bonuses.get(cid, original.get("world_bonus")),
            ))
        except (UserDataError, ValueError, KeyError, TypeError):
            unresolved.append(cid)
        if not any(row.get("source_group") == "resolved" for row in stats.get(cid, [])):
            unresolved.append(cid)
    unresolved = sorted(set(unresolved) | (ids - profiles.keys()))
    conflict = reason if reason and reason.startswith("native_cross_half_") else None
    result = {**deepcopy(original), "freeze_phase": "settlement_native", "snapshot_id": None,
              "profiles": profiles, "stat_snapshots": stats, "equipment": equipment,
              "native_scope_validation": selected["native_scope_validation"],
              "native_conflict_character_ids": sorted(conflicted),
              "settlement_fallback": fallback_evidence,
              "calculation_unavailable_reason": conflict or ("character_calculation_inputs_missing" if unresolved else None),
              "unresolved_character_ids": unresolved}
    if not service._context_is_current(service._dependencies):
        raise UserDataError("战报账号上下文已经变化")
    return dao.freeze_battle_settlement_build(
        operation_id, result, account_generation=service._dependencies.generation)


def settlement_warning(build):
    fallback = build.get("settlement_fallback") or {}
    unresolved = build.get("unresolved_character_ids") or []
    messages = []
    labels = {"combat": "本场", "upper": "上半场", "lower": "下半场"}
    incomplete = [labels.get(scope, scope) for scope, row in (build.get("native_scope_validation") or {}).items()
                  if row.get("state") != "ready"]
    if incomplete:
        messages.append("、".join(incomplete) + "首击快照不齐，已保留现有证据")
    if fallback:
        messages.append(f"{len(fallback)} 名角色的首击计算输入缺失，已使用结算时数据库配置补齐并标记来源")
    if unresolved:
        messages.append(f"{len(unresolved)} 名角色仍缺少可计算输入，其他角色与原始逐击已保留")
    if str(build.get("calculation_unavailable_reason") or "").startswith("native_cross_half_"):
        messages.append("上下半场存在不同配置，分半场原始证据已保留，统一计算副本不可用")
    return "；".join(messages) + "。" if messages else None
