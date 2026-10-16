# 共用冻结配装事实、占用身份与最终合法性复核，不计算新伤害目标或执行搜索。
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from typing import Any

from src.domain.crit_threshold import BASE_CRIT_RATE, crit_floor_enabled, loadout_crit_total, normalize_preference_config
from src.domain.suit_identity import tape_matches_suit_target
from src.integrations.nte_analysis_core import NativeAnalysisError
from src.optimizer.contracts import plan_drives


def allocation_plan_wire(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return allocation_plan_wire(value.model_dump())
    if isinstance(value, Mapping):
        return {str(key): allocation_plan_wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [allocation_plan_wire(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [allocation_plan_wire(item) for item in sorted(value)]
    return value


def allocation_inputs_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(allocation_plan_wire(value), sort_keys=True, ensure_ascii=False, allow_nan=False,
                                   separators=(",", ":")).encode("utf-8")).hexdigest()


def locked_allocation_owners(dao) -> tuple[tuple[Any, ...], ...]:
    """仅冻结计算配装锁；未锁定旧方案不是候选排除条件，借出由保存事务处理。

    不排除本次参与角色：同角色其他槽位的锁也有效。修订随锁一起冻结，保存前复核。
    本次新方案之间的 UID 互斥由求解器及结果验收负责，不借旧方案占用替代。
    """
    return tuple(sorted((int(row["plan_id"]), int(row["character_id"]), str(row["kind"]),
                         int(row["uid_slot"]), int(row["uid_serial"]), str(row["updated_at_utc"]))
                        for row in dao.list_allocation_locked_equipment_owners()))


def postprocess_first_scan_cut(group, *, improvements=0):
    """首轮尚未完整且未接受任何改善的组只留B0，不进入优化保存集合；不影响未锁定旧方案装备的借用。"""
    return (group["termination"] in {"evaluation_budget", "round_budget", "enumeration_budget", "time_budget"}
            and not group.get("single_complete", False) and not group.get("improvements", improvements))


def verify_postprocess_hard_constraints(request, scorer, baseline_plans, plans, selected_role, *, actual_main_values=None, base_scores=None, native_verified=False):
    """只接纳冻结 v2 硬合法性；评级/词条顺序保持软偏好，不重新评分或搜索。"""
    plan = plans[selected_role]
    drives, tape = list(plan_drives(plan)), plan.get("assigned_tape")
    config = request.stat_priority_configs.get(selected_role)
    preference = normalize_preference_config(config or {})
    catalog = scorer.stat_catalog

    def stat_name(raw):
        raw = str(raw or "").strip()
        return catalog.normalize_stat_name(raw, is_percent="%" in raw) or raw

    blocked = {stat_name(raw) for raw in preference["blacklist"]}
    if not preference["blacklist_zero_weight"] and any(
        blocked.intersection(stat_name(raw) for raw in drive.sub_stats) for drive in drives
    ):
        raise NativeAnalysisError("装备后处理违反冻结驱动黑名单")
    old = baseline_plans[selected_role]
    mains = tuple(request.core_main_filters.get(selected_role, ()))
    if old.get("main_filter_fallback"):
        if tuple(old.get("requested_core_main_filters") or ()) != mains:
            raise NativeAnalysisError("装备后处理的主词条回退来源不一致")
        mains = ()
    for key in ("requested_core_main_filters", "main_filter_fallback", "main_filter_fallback_rejected"):
        if plan.get(key) != old.get(key):
            raise NativeAnalysisError("装备后处理修改了冻结主词条回退事实")
    allowed_mains = {name for raw in mains for name in (str(raw), catalog.normalize_tape_main_stat(raw))}
    if allowed_mains and (tape.main_stats not in allowed_mains
                          and catalog.normalize_tape_main_stat(tape.main_stats) not in allowed_mains):
        raise NativeAnalysisError("装备后处理违反冻结卡带主词条")
    role = request.roles_db[selected_role]
    target = request.core_set_targets.get(selected_role) if selected_role in request.core_set_targets else (
        request.module_set_targets.get(selected_role, role.get("default_set", "")))
    if not tape_matches_suit_target(tape, target, request.sets_db):
        raise NativeAnalysisError("装备后处理违反冻结套装目标")
    # 完整作业已经在同一 Rust 内存模型验证数值硬约束；Python 仍复核身份、集合和基础分证明。
    if native_verified and base_scores is None:
        raise NativeAnalysisError("完整作业缺少冻结基础评分证明")
    if not native_verified:
        physical_tape = tape
        if actual_main_values is not None:
            if tape.uid not in actual_main_values:
                raise NativeAnalysisError("实际卡带主词条来源缺失")
            physical_tape = {**allocation_plan_wire(tape), "main_value": actual_main_values[tape.uid]}
        total = loadout_crit_total(role, physical_tape, drives, alias_mapping=catalog.stat_alias_mapping,
                                  tape_main_values=catalog.tape_main_values)
        cap = (request.crit_rate_caps or {}).get(selected_role)
        if cap is not None and BASE_CRIT_RATE + total > float(cap) + 1e-9:
            raise NativeAnalysisError("装备后处理违反冻结暴击上限")
        if crit_floor_enabled(config):
            if not role.get("crit_source_known"):
                raise NativeAnalysisError("装备后处理缺少冻结暴击下限来源")
            # 正式 v2 floor 消费冻结非装备投影；基础 5% 只累加一次，不使用旧 fork_crit_rate。
            current = round(BASE_CRIT_RATE + total + float(role.get("non_equipment_crit_rate", 0.0)), 4)
            if current + 1e-9 < preference["crit_threshold"]:
                raise NativeAnalysisError("装备后处理违反冻结暴击下限")
    # 旧入口仍沿唯一 ScoringEngine；扩展入口一次向同一 Rust 公共核批量核对，不逐件跨进程。
    if base_scores is None:
        zero_weight_stats = preference["blacklist"] if preference["blacklist_zero_weight"] else ()
        weights = role.get("weights") or {}
        maximum = scorer.max_theoretical_weight(weights, zero_weight_stats=zero_weight_stats)
        expected_scores = {item.uid: (scorer.calculate_drive_score(item, weights, maximum, zero_weight_stats=zero_weight_stats)
                           if item.item_type == "drive" else scorer.calculate_cartridge_score(
                               item, weights, maximum, role.get("main_weights"))) for item in [tape, *drives]}
    else:
        expected_scores = base_scores
    for item in [tape, *drives]:
        if (item.uid not in expected_scores or selected_role not in item.role_scores
                or not math.isclose(item.role_scores[selected_role], expected_scores[item.uid], rel_tol=1e-9, abs_tol=1e-8)):
            raise NativeAnalysisError("装备后处理改变了冻结基础装备评分")





def allocation_role_state_from_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """纯内存布局转换供预览和保存共用，不读旧 JSON 或当前装备。"""
    board = [["XX" if (type(cell) is int and cell < 0) else "0" if cell == 0 else str(cell) for cell in row]
             for row in plan.get("blueprint", {}).get("board", []) or []]
    tape = plan.get("assigned_tape")
    result = {"blueprint_layout": board, "equipped_tape": {"uid": tape.uid} if tape is not None else None,
              "equipped_drives": [{"uid": drive.uid, "shape_id": drive.shape_id} for drive in plan_drives(plan)]}
    if "native_layout" in plan:
        from copy import deepcopy
        result["native_layout"] = deepcopy(plan["native_layout"])
    return result


def restore_postprocess_results(request, scorer, baseline_plans, wire_plans, roles, reserved_uids, *, actual_main_values=None, allow_blueprint_changes=False, base_scores=None, native_verified=False):
    """恢复已返回方案并独立验收固定槽位、基础分、UID和硬约束；没有第二搜索或目标公式。"""
    from copy import deepcopy
    from src.integrations.native_allocation import restore
    baseline = allocation_plan_wire(baseline_plans)
    accepted = set(roles)
    if any(wire_plans.get(role) != old for role, old in baseline.items() if role not in accepted):
        raise NativeAnalysisError("后处理改变了未接纳角色的基线")
    for role in accepted:
        old, new = baseline[role], wire_plans[role]
        if new.get("valid") is not True:
            raise NativeAnalysisError("后处理返回了无效方案")
        if allow_blueprint_changes:
            before, after = old.get("blueprint") or {}, new.get("blueprint") or {}
            def blocked(board):
                return [[cell == "XX" or type(cell) is int and cell < 0 for cell in row] for row in board]
            if (after not in request.blueprints_db.get(role, ())
                    or before.get("set_effect_mode") != after.get("set_effect_mode")
                    or blocked(before.get("board") or []) != blocked(after.get("board") or [])
                    or not isinstance(new.get("native_layout"), Mapping)):
                raise NativeAnalysisError("后处理的新图纸不属于冻结棋盘和模式，或缺少原生落位证明")
        elif old.get("blueprint") != new.get("blueprint"):
            raise NativeAnalysisError("后处理改变了完整冻结布局")
        for key in (() if allow_blueprint_changes else ("assigned_set_drives", "assigned_extra_drives")):
            if len(old[key]) != len(new[key]) or any(any(a.get(field) != b.get(field) for field in
                ("item_type", "shape_id", "area", "suit_id", "set_name")) for a, b in zip(old[key], new[key])):
                raise NativeAnalysisError("后处理改变了冻结驱动槽位签名")
        if any(old["assigned_tape"].get(key) != new["assigned_tape"].get(key) for key in ("suit_id", "set_name", "shape_id", "area")):
            raise NativeAnalysisError("后处理改变了冻结卡带签名")
    plans = restore(deepcopy(wire_plans), request)
    occupied = set()
    for role, plan in plans.items():
        for item in [*plan_drives(plan), plan.get("assigned_tape")]:
            if item is None or not item.uid.startswith(("nte-module-", "nte-core-")):
                continue
            if item.uid in occupied or role in accepted and item.uid in reserved_uids:
                raise NativeAnalysisError("后处理重复消费或借用了冻结真实UID")
            occupied.add(item.uid)
        if role in accepted:
            verify_postprocess_hard_constraints(request, scorer, baseline_plans, plans, role, actual_main_values=actual_main_values,
                                               base_scores=None if base_scores is None else base_scores[role], native_verified=native_verified)
    return plans


def allocation_save_projection(role, plan, snapshot_id, character_id):
    tape = plan["assigned_tape"]
    return {"role_name": role, "character_id": character_id, "snapshot_id": snapshot_id, "score": plan["score"],
        "role_state": allocation_role_state_from_plan(plan),
        "assignment_scores": {item.uid: item.role_scores[role] for item in [tape, *plan_drives(plan)]},
        "tape_main_values": {tape.uid: tape.main_value}}


def allocation_row_save_projection(row):
    result = {key: row.get(key) for key in ("role_name", "character_id", "snapshot_id", "score", "role_state")}
    result.update({key: (row.get("payload") or {}).get(key) for key in ("assignment_scores", "tape_main_values")})
    return result


def freeze_postprocess_allocation_request(request, scorer):
    """只规范化新作业副本的旧 strict 默认；B0 原请求、receipt和上游行为不变。"""
    from src.integrations.native_allocation_wire import freeze
    result = freeze(request, scorer)["request"]
    if not result["groups"]:
        result["groups"] = [[index] for index in result["role_order"]]
    return result


def verify_optimization_batch_rows(rows, *, shared_axis=False):
    """一个保存事务只消费同一预览作业；不能拼接不同作业的成员而复用未评价的组目标。"""
    if not rows:
        return
    metadata = [(row.get("payload") or {}).get("damage_optimization") or {} for row in rows]
    identity = metadata[0].get("input_identity")
    if not isinstance(identity, Mapping) or any(value.get("input_identity") != identity for value in metadata):
        raise RuntimeError("优化组成员来自不同预览作业，请保存同一次完整预览。")
    if shared_axis:
        shared = {key: metadata[0].get(key) for key in ("source_input_digest", "fixed_team_build_sha256", "fixed_team_members", "frozen_identity", "scope", "baseline_value", "candidate_value", "delta", "shared_value", "groups", "exchange_links", "shared_build_dependencies", "shared_build_dependencies_sha256")}
        if any(any(value.get(key) != expected for key, expected in shared.items()) for value in metadata):
            raise RuntimeError("同轴全队目标与冻结预览组合不一致，请重新计算。")
    groups = {}
    for value in metadata:
        if "group_index" not in value:
            continue
        key = value["group_index"]
        members = value.get("group_members")
        if key in groups and groups[key] != members:
            raise RuntimeError("优化组的冻结成员或最终目标不一致，请重新计算。")
        groups[key] = members


def linked_save_dependencies(groups, links):
    """合并本次原子交换及原同级组的保存依赖；只处理冻结身份，不重新计算目标。"""
    dependencies = {m["role"]: {n["role"] for n in g["members"]}
                    for g in groups for m in g["members"]}
    for left, right in links:
        joined = dependencies[left] | dependencies[right]
        for role in joined:
            dependencies[role] = joined
    return {role: sorted(members) for role, members in dependencies.items()}
