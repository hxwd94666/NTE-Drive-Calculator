# 验收一次原生多组作业的冻结分区、预算和目标事实，不在Python搜索或另算伤害。
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import math

from src.integrations.battle_axis_evaluation import (
    CRITICAL_POLICY, decode_axis_response_facts, validate_axis_equipment, validate_axis_selector,
)
from src.integrations.nte_analysis_core import MAX_BYTES, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, _json_object


DIRECT_BATCH = "allocation_group_postprocess_v1"
AXIS_BATCH = "battle_axis_group_postprocess_v1"
TERMINATIONS = {"local_optimum", "not_ready", "model_gap", "evaluation_budget", "round_budget", "enumeration_budget", "time_budget"}
DIRECT_FIELDS = {"objective", "model_id", "neighborhood", "evaluations", "rounds", "candidate_visits",
                 "termination", "neighborhood_complete", "groups", "exchange_links"}
AXIS_FIELDS = {"objective", "model_id", "shared_value", "team_event_scope", "baseline_value", "candidate_value", "delta",
    "evaluations", "rounds", "improvements", "candidate_visits", "unquantified_candidates", "gaps", "termination", "reason",
    "enumeration_complete", "quantification_complete", "neighborhood_complete", "groups", "selection_policy", "exchange_links"}
GROUP_FIELDS = {"group_index", "members", "evaluations", "rounds", "improvements", "termination", "reason",
                "single_complete", "pair_complete", "exchange_complete", "quantification_complete", "neighborhood_complete"}


def _error():
    return NativeAnalysisError("多组装备优化协议或冻结目标事实无效")


def _integer(value, minimum=0, maximum=2**63 - 1):
    return type(value) is int and minimum <= value <= maximum


def _number(value, *, positive=False, signed=False):
    return type(value) in (int, float) and math.isfinite(value) and (signed or value > 0 if positive else signed or value >= 0)


def _same(a, b):
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-7)


def _identity(value):
    return (isinstance(value, dict) and set(value) == {"request_id", "input_sha256"}
        and isinstance(value["request_id"], str) and 0 < len(value["request_id"].encode("utf8")) <= 256
        and isinstance(value["input_sha256"], str) and len(value["input_sha256"]) == 64
        and all(c in "0123456789abcdef" for c in value["input_sha256"]))


def _request(request, *, axis):
    expanded = request.get("version") == 2
    projected = not axis and expanded
    required = {"version", "objective", "input_identity", "allocation_request", "baseline_plans", "reserved_uids",
                "role_character_ids", "budget", "neighborhood"}
    required |= {"battle_request", "anchor_mode", "equipment_value_mode", "baseline_equipment_by_character", "inventory_equipment_by_uid", "team_preflight"} if axis else {"projection_inputs"} if projected else {"role_models"}
    objective = "frozen_axis_expected_v1" if axis else "standard_direct_v1"
    if (set(request) != required or type(request["version"]) is not int or request["version"] != (2 if expanded else 1)
            or request["objective"] != objective
            or request["neighborhood"] != ("single_pair_exchange_blueprint" if expanded else "single_pair_exchange") or not _identity(request["input_identity"])):
        raise _error()
    budget = request["budget"]
    if (not isinstance(budget, dict) or not {"max_evaluations", "max_rounds"} <= budget.keys()
            or not budget.keys() <= {"max_evaluations", "max_rounds", "max_visits", "max_millis"}
            or not _integer(budget["max_evaluations"], 1, 100_000) or not _integer(budget["max_rounds"], 1, 1_000)
            or not _integer(budget.get("max_visits", 1_000_000), 1, 1_000_000)
            or not _integer(budget.get("max_millis", 120_000), 1, 120_000)):
        raise _error()
    allocation = request["allocation_request"]
    if not isinstance(allocation, dict) or type(allocation.get("version")) is not int or allocation["version"] != 2:
        raise _error()
    roles, groups = allocation["roles"], allocation["groups"]
    if not isinstance(roles, list) or not roles or any(not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"] for row in roles):
        raise _error()
    names = [row["name"] for row in roles]
    if (len(set(names)) != len(names) or not isinstance(groups, list) or not groups or any(not isinstance(g, list) or not g for g in groups)
            or any(not _integer(i, 0, len(roles) - 1) for group in groups for i in group)
            or sorted(i for group in groups for i in group) != list(range(len(roles)))
            or sorted(allocation["role_order"]) != list(range(len(roles)))):
        raise _error()
    plans, ids = request["baseline_plans"], request["role_character_ids"]
    if (not isinstance(plans, dict) or set(plans) != set(names) or any(not isinstance(plan, dict) for plan in plans.values())
            or not isinstance(ids, dict) or not set(ids) <= set(names) or any(not _integer(v, 1) for v in ids.values())
            or len(set(ids.values())) != len(ids)):
        raise _error()
    reserved = request["reserved_uids"]
    if (not isinstance(reserved, list) or len(reserved) > 100_000 or len(set(reserved)) != len(reserved)
            or any(not isinstance(uid, str) or not uid or len(uid.encode("utf8")) > 256 for uid in reserved)):
        raise _error()
    if axis:
        from src.integrations.axis_fixed_team import validate_preflight_proof
        validate_preflight_proof(request["team_preflight"])
        validate_axis_selector(request["battle_request"])
        if request["equipment_value_mode"] != "actual_level" or request["anchor_mode"] not in {"original", "effective"}:
            raise _error()
        builds, actual = request["baseline_equipment_by_character"], request["inventory_equipment_by_uid"]
        if not isinstance(actual, dict) or set(actual) != {item["uid"] for item in allocation["inventory"]}:
            raise _error()
        validate_axis_equipment(list(actual.values()), complete=False)
        if any(uid != f"nte-{item['kind']}-{item['uid_slot']}-{item['uid_serial']}" for uid, item in actual.items()):
            raise _error()
        if not isinstance(builds, list) or len(builds) > 16:
            raise _error()
        characters, occupied = set(), set()
        for row in builds:
            if (not isinstance(row, dict) or set(row) != {"character_id", "equipment"} or not _integer(row["character_id"], 1)
                    or row["character_id"] in characters):
                raise _error()
            uids = validate_axis_equipment(row["equipment"])
            if uids & occupied:
                raise _error()
            occupied.update(uids)
            characters.add(row["character_id"])
    elif projected:
        inputs = request["projection_inputs"]
        if (not isinstance(inputs, dict) or set(inputs) != {"details", "inventory", "geometry_cells"}
                or not isinstance(inputs["details"], dict) or not set(inputs["details"]) <= set(names)
                or not isinstance(inputs["geometry_cells"], dict) or len(inputs["geometry_cells"]) > 128
                or not isinstance(inputs["inventory"], dict)
                or set(inputs["inventory"]) != {item["uid"] for item in allocation["inventory"]}
                or any(not isinstance(row, dict) for row in inputs["inventory"].values())):
            raise _error()
        detail_fields = {"character", "profile", "growth_rows", "forks", "shape_bonus", "attributes",
                         "likeability_bonus", "world_bonus"}
        for role, detail in inputs["details"].items():
            if (not isinstance(detail, dict) or set(detail) != detail_fields
                    or not isinstance(detail["character"], dict)
                    or detail["character"].get("character_id") != ids.get(role)
                    or any(not isinstance(detail[key], list) for key in ("growth_rows", "forks"))
                    or any(not isinstance(detail[key], dict) for key in detail_fields - {"growth_rows", "forks"})):
                raise _error()
    else:
        models = request["role_models"]
        if (not isinstance(models, dict) or not set(models) <= set(names)
                or any(not isinstance(model, dict) or not isinstance(model.get("model_id"), str)
                       or not isinstance(model.get("baseline"), dict) or not isinstance(model.get("item_values"), dict)
                       or not _number(model.get("crit_rate_cap")) or model["crit_rate_cap"] > 1 for model in models.values())):
            raise _error()
    return [[names[index] for index in group] for group in groups]


def _groups(value, request, expected, *, axis):
    if not isinstance(value, list) or len(value) != len(expected):
        raise _error()
    result = []
    for index, group in enumerate(value):
        fields = GROUP_FIELDS | ({"enumeration_complete"} if axis else {"objective_value", "candidate_visits"})
        if request["version"] == 2:
            fields |= {"blueprint_complete"}
        if (not isinstance(group, dict) or not fields <= group.keys() or type(group["group_index"]) is not int
                or group["group_index"] != index or group["termination"] not in TERMINATIONS or not isinstance(group["reason"], str)):
            raise _error()
        for key, maximum in (("evaluations", request["budget"]["max_evaluations"]), ("rounds", request["budget"]["max_rounds"]),
                             ("improvements", request["budget"]["max_rounds"])):
            if not _integer(group[key], 0, maximum):
                raise _error()
        coverage = [key for key in fields if key.endswith("_complete")]
        if (any(type(group[key]) is not bool for key in coverage) or group["improvements"] > group["rounds"]
                or group["neighborhood_complete"] != all(group[key] for key in coverage if key != "neighborhood_complete")
                or (group["termination"] == "local_optimum") != group["neighborhood_complete"]):
            raise _error()
        members = group["members"]
        if (not isinstance(members, list) or len(members) != len(expected[index])
                or any(not isinstance(member, dict) or not isinstance(member.get("role"), str) for member in members)
                or sorted(member["role"] for member in members) != sorted(expected[index])):
            raise _error()
        frozen = {key: deepcopy(group[key]) for key in fields}
        checked = []
        for member in members:
            role = member["role"]
            if member.get("character_id") != request["role_character_ids"].get(role) or member.get("character_id") is not None and not _integer(member["character_id"], 1):
                raise _error()
            keys = {"role", "character_id"} if axis else {"role", "character_id", "baseline_value", "candidate_value", "relative_gain"}
            if not keys <= member.keys() or axis and set(member) != keys:
                raise _error()
            if not axis:
                baseline, candidate, gain = (member[key] for key in ("baseline_value", "candidate_value", "relative_gain"))
                if (baseline is not None and not _number(baseline, positive=True) or candidate is not None and not _number(candidate)
                        or baseline is not None and candidate is not None and candidate < baseline
                        or gain is not None and not _number(gain, signed=True)
                        or gain is not None and (baseline is None or candidate is None or not _same(candidate / baseline - 1, gain))):
                    raise _error()
                if group["termination"] != "not_ready" and group["quantification_complete"] and any(v is None for v in (baseline, candidate, gain)):
                    raise _error()
            checked.append({key: deepcopy(member[key]) for key in keys})
        frozen["members"] = checked
        if not axis:
            if not _integer(group["candidate_visits"], 0, 1_000_000):
                raise _error()
            objective = group["objective_value"]
            gains = [member["relative_gain"] for member in checked]
            if objective is not None and (not _number(objective) or any(g is None for g in gains) or not _same(objective, sum(gains) / len(gains))):
                raise _error()
            if group["quantification_complete"] and objective is None:
                raise _error()
        if group["termination"] == "not_ready" and group["improvements"]:
            raise _error()
        result.append(frozen)
    return result


def _native_layout(plans, roles):
    """这里只验收原生落位结构和装备身份；正式形状与占用格在冻结保存门禁复核。"""
    for role in roles:
        plan = plans[role]
        proof = plan.get("native_layout")
        tape = plan.get("assigned_tape") or {}
        drives = [*plan.get("assigned_set_drives", ()), *plan.get("assigned_extra_drives", ())]
        if (not isinstance(proof, dict) or set(proof) != {"version", "tape_uid", "drives"}
                or not _integer(proof["version"], 1, 1) or proof["tape_uid"] != tape.get("uid")
                or not isinstance(proof["drives"], list) or len(proof["drives"]) != len(drives)):
            raise _error()
        expected = {item["uid"]: item["shape_id"] for item in drives}
        used = set()
        for placed in proof["drives"]:
            if (not isinstance(placed, dict) or set(placed) != {"uid", "shape_id", "target_row", "target_column"}
                    or not isinstance(placed["uid"], str) or placed["uid"] in used
                    or expected.get(placed["uid"]) != placed["shape_id"]
                    or any(not _integer(placed[key], 1, 5) for key in ("target_row", "target_column"))):
                raise _error()
            used.add(placed["uid"])
        if used != set(expected) or len(used) != len(drives):
            raise _error()


def _response(client, result, request, expected, *, axis):
    batch = AXIS_BATCH if axis else DIRECT_BATCH
    fields = AXIS_FIELDS if axis else DIRECT_FIELDS
    expanded = request["version"] == 2
    projected = not axis and expanded
    if expanded:
        fields |= {"budget_policy", "elapsed_millis"}
    if projected:
        fields |= {"projection_policy"}
    model = "frozen_axis_expected_v1" if axis else "role_panel_standard_direct_v1"
    if (result.get("schema_version") != RESPONSE_SCHEMA or result.get("engine_version") != client.engine_version
            or result.get("dataset_version") != client.dataset_version or result.get("batch_kind") != batch
            or type(result.get("version")) is not int or result["version"] != request["version"] or "error" in result
            or result.get("input_identity") != request["input_identity"] or not isinstance(result.get("plans"), dict)
            or set(result["plans"]) != set(request["baseline_plans"]) or any(not isinstance(p, dict) for p in result["plans"].values())):
        raise _error()
    value = result.get("optimization")
    if (not isinstance(value, dict) or not fields <= value.keys() or value["objective"] != request["objective"]
            or value["model_id"] != model or value["termination"] not in TERMINATIONS or type(value["neighborhood_complete"]) is not bool):
        raise _error()
    for key, maximum in (("evaluations", request["budget"]["max_evaluations"]), ("rounds", request["budget"]["max_rounds"]), ("candidate_visits", request["budget"].get("max_visits", 1_000_000))):
        if not _integer(value[key], 0, maximum):
            raise _error()
    if expanded and (value["budget_policy"] != "balanced_neighborhoods_v1" or not _integer(value["elapsed_millis"])):
        raise _error()
    if projected and value["projection_policy"] != "raw_official_panel_rust_v1":
        raise _error()
    raw_groups = value["groups"]
    # 轴在完整 B0 都未物化时没有搜索组，明确 not_ready；绝不猜缺身份角色是否参战。
    groups = [] if axis and raw_groups == [] and value["termination"] == "not_ready" else _groups(raw_groups, request, expected, axis=axis)
    if value["neighborhood_complete"] != (bool(groups) and all(g["neighborhood_complete"] for g in groups)):
        raise _error()
    if (value["termination"] == "local_optimum") != value["neighborhood_complete"]:
        raise _error()
    for group in groups:
        if group["termination"] == "not_ready" and any(result["plans"][role] != request["baseline_plans"][role] for role in expected[group["group_index"]]):
            raise _error()
    links = value["exchange_links"]
    ready = {m["role"] for g in groups if g["termination"] != "not_ready" for m in g["members"]}
    if (not isinstance(links, list) or len(links) > request["budget"]["max_rounds"]
            or any(not isinstance(link, list) or len(link) != 2
                   or any(not isinstance(role, str) or role not in ready for role in link)
                   or link[0] == link[1] for link in links)
            or len({tuple(sorted(link)) for link in links}) != len(links)):
        raise _error()
    if expanded:
        eligible = {m["role"] for g in groups if g["termination"] != "not_ready"
                    and (g["improvements"] > 0 or g["single_complete"]) for m in g["members"]}
        _native_layout(result["plans"], eligible)
    optimization = {key: deepcopy(value[key]) for key in fields}
    optimization["groups"] = groups
    output = {key: deepcopy(result[key]) for key in ("schema_version", "engine_version", "dataset_version", "batch_kind", "version", "input_identity", "plans")}
    if axis:
        if (result.get("model_id") != model or result.get("equipment_value_mode") != "actual_level"
                or result.get("critical_policy") != CRITICAL_POLICY or result.get("ordering_policy") != "previous_single_exact_delta_then_uid"
                or result.get("neighborhood") != ("single_pair_exchange_blueprint" if expanded else "single_pair_exchange_fixed_signature") or value["shared_value"] is not True
                or value["team_event_scope"] != "all_frozen_axis_events_v1"
                or value["selection_policy"] != "whole_team_best_move_v1" or not isinstance(value["reason"], str)
                or not _integer(value["improvements"], 0, value["rounds"]) or not _integer(value["unquantified_candidates"])
                or not isinstance(value["gaps"], dict) or any(not isinstance(k, str) or not k or not _integer(v, 1) for k, v in value["gaps"].items())):
            raise _error()
        for key in ("enumeration_complete", "quantification_complete"):
            if type(value[key]) is not bool or value[key] != (bool(groups) and all(g[key] for g in groups)):
                raise _error()
        # 与单角色轴作业一致：存在未量化候选时不能声称完整邻域已无改善。
        # 仅拒绝互相矛盾的协议事实，不把未知候选当作零收益参与比较。
        if value["termination"] == "local_optimum" and (value["unquantified_candidates"] or value["gaps"]):
            raise _error()
        selected = result.get("selected_result")
        if result.get("frozen_identity") is None:
            if (value["termination"] != "not_ready" or any(result.get(k) is not None for k in ("scope", "baseline", "selected_result", "source_input_digest"))
                    or result["plans"] != request["baseline_plans"]):
                raise _error()
            facts = {key: None for key in ("source_input_digest", "frozen_identity", "scope", "baseline")}
            facts["candidates"] = []
        else:
            facts = decode_axis_response_facts({**result, "candidates": [] if selected is None else [selected]}, request, () if selected is None else ("selected",))
        before, after, delta = (value[key] for key in ("baseline_value", "candidate_value", "delta"))
        if selected is None:
            if value["termination"] != "not_ready" or groups or after is not None or delta is not None or value["improvements"]:
                raise _error()
            if facts["baseline"] is not None and before != facts["baseline"]["candidate_value"]:
                raise _error()
        elif (not all(_number(v) for v in (before, after, delta)) or after < before or not _same(after - before, delta)
                or facts["baseline"]["candidate_value"] is None or not _same(before, facts["baseline"]["candidate_value"])
                or facts["candidates"][0]["candidate_value"] is None or not _same(after, facts["candidates"][0]["candidate_value"])):
            raise _error()
        output.update({key: deepcopy(result[key]) for key in ("model_id", "equipment_value_mode", "critical_policy", "ordering_policy", "neighborhood")})
        output.update({key: facts[key] for key in ("source_input_digest", "frozen_identity", "scope", "baseline")})
        output["selected_result"] = facts["candidates"][0] if facts["candidates"] else None
    elif value["neighborhood"] != ("single_pair_exchange_blueprint" if projected else "single_pair_exchange"):
        raise _error()
    output["optimization"] = optimization
    return output


def postprocess_allocation_groups(client, request: Mapping, *, axis=False, checkpoint=None):
    batch = AXIS_BATCH if axis else DIRECT_BATCH
    client._checkpoint(checkpoint)
    if not getattr(client, "supports_battle_axis_group_postprocess" if axis else "supports_allocation_group_postprocess", False):
        raise NativeAnalysisError("多组装备优化组件未就绪，请更新配套分析组件")
    if request.get("version") == 2 and not getattr(client,
            "supports_battle_axis_group_rematch" if axis else "supports_allocation_group_projection", False):
        raise NativeAnalysisError("多组优化组件缺少扩展图纸与原生布局能力，请更新配套分析组件")
    try:
        _request(request, axis=axis)
        payload = json.dumps({"schema_version": REQUEST_SCHEMA, "batch_kind": batch, "dataset_version": client.dataset_version,
            "request": request}, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
        if len(payload) > MAX_BYTES:
            raise _error()
        request = json.loads(payload)["request"]
        groups = _request(request, axis=axis)
        result = _response(client, _json_object(client._run(payload, checkpoint=checkpoint)), request, groups, axis=axis)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise _error() from None
    client._checkpoint(checkpoint)
    return result
