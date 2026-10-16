# 验收正式轴搜索协议、真实库存身份和完整收益，复用轴事实decoder而不评价或枚举候选。
from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
from typing import Any

from src.integrations.battle_axis_evaluation import (
    CRITICAL_POLICY, MODEL_ID, REQUEST_KEYS, decode_axis_response_facts, validate_axis_equipment, validate_axis_request,
)
from src.integrations.nte_analysis_core import MAX_BYTES, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, _json_object


BATCH_KIND = "battle_axis_postprocess_v1"
OPTIMIZATION_FIELDS = frozenset({"objective", "model_id", "selected_role", "baseline_value", "candidate_value", "delta",
    "evaluations", "rounds", "improvements", "termination", "reason", "enumeration_complete", "quantification_complete",
    "neighborhood_complete", "unquantified_candidates", "gaps"})
SEARCH_FIELDS = frozenset({"objective", "allocation_request", "baseline_plans", "selected_role", "role_character_ids",
                           "reserved_uids", "inventory_equipment_by_uid", "budget", "team_preflight"})


def _error():
    return NativeAnalysisError("战报轴装备优化协议或冻结事实无效")


def _integer(value, minimum=0, maximum=2**63 - 1):
    return type(value) is int and minimum <= value <= maximum


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0.0
    except OverflowError:
        return False


def _same(left, right):
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-7)


def _validate_request(request):
    if set(request) != (REQUEST_KEYS - {"candidates"}) | SEARCH_FIELDS or request["objective"] != MODEL_ID:
        raise _error()
    validate_axis_request({**{key: request[key] for key in REQUEST_KEYS - {"candidates"}}, "candidates": []})
    from src.integrations.axis_fixed_team import validate_preflight_proof
    validate_preflight_proof(request["team_preflight"])
    budget = request["budget"]
    if (not isinstance(budget, dict) or set(budget) != {"max_evaluations", "max_rounds"}
            or not _integer(budget["max_evaluations"], 1, 100_000) or not _integer(budget["max_rounds"], 1, 1_000)):
        raise _error()
    allocation = request["allocation_request"]
    plans, roles, inventory = request["baseline_plans"], request["role_character_ids"], request["inventory_equipment_by_uid"]
    if (not isinstance(allocation, dict) or type(allocation.get("version")) is not int or allocation["version"] != 2
            or not isinstance(allocation.get("roles"), list) or not isinstance(allocation.get("inventory"), list)
            or not isinstance(plans, dict) or any(not isinstance(plan, dict) for plan in plans.values())
            or not isinstance(roles, dict) or not isinstance(inventory, dict)):
        raise _error()
    role_names = {row["name"] for row in allocation["roles"]}
    if (request["selected_role"] not in role_names or roles.get(request["selected_role"]) != request["selected_character_id"]
            or not set(roles) <= role_names or not all(_integer(value, 1) for value in roles.values())
            or len(set(roles.values())) != len(roles)):
        raise _error()
    uids = [item["uid"] for item in allocation["inventory"]]
    if len(uids) != len(inventory) or set(uids) != set(inventory):
        raise _error()
    validate_axis_equipment(list(inventory.values()), complete=False)
    for uid, item in inventory.items():
        if uid != f"nte-{item['kind']}-{item['uid_slot']}-{item['uid_serial']}":
            raise _error()
    reserved = request["reserved_uids"]
    if (not isinstance(reserved, list) or len(reserved) > 100_000
            or any(not isinstance(value, str) or not value or len(value.encode("utf8")) > 256 for value in reserved)):
        raise _error()


def _optimization(value: Any, request, facts):
    if (not isinstance(value, dict) or not OPTIMIZATION_FIELDS <= value.keys()
            or value["objective"] != MODEL_ID or value["model_id"] != MODEL_ID
            or value["selected_role"] != request["selected_role"] or not isinstance(value["reason"], str)):
        raise _error()
    for key, limit in (("evaluations", request["budget"]["max_evaluations"]),
                       ("rounds", request["budget"]["max_rounds"]), ("improvements", request["budget"]["max_rounds"])):
        if not _integer(value[key], 0, limit):
            raise _error()
    if (not _integer(value["unquantified_candidates"]) or value["improvements"] > value["rounds"]
            or any(type(value[key]) is not bool for key in ("enumeration_complete", "quantification_complete", "neighborhood_complete"))
            or value["neighborhood_complete"] != (value["enumeration_complete"] and value["quantification_complete"])):
        raise _error()
    gaps = value["gaps"]
    if (not isinstance(gaps, dict) or any(not isinstance(code, str) or not code or not _integer(count, 1) for code, count in gaps.items())):
        raise _error()
    termination = value["termination"]
    if termination == "not_ready":
        if (any(value[key] is not None for key in ("baseline_value", "candidate_value", "delta"))
                or value["improvements"] or value["neighborhood_complete"]):
            raise _error()
    else:
        before, after, delta = (value[key] for key in ("baseline_value", "candidate_value", "delta"))
        if (termination not in {"local_optimum", "model_gap", "evaluation_budget", "round_budget"}
                or not all(_number(number) for number in (before, after, delta)) or after < before or not _same(after - before, delta)
                or not value["evaluations"] or facts["baseline"]["candidate_value"] is None
                or facts["candidates"][0]["candidate_value"] is None
                or not _same(before, facts["baseline"]["candidate_value"]) or not _same(after, facts["candidates"][0]["candidate_value"])):
            raise _error()
        if termination == "local_optimum":
            if not value["neighborhood_complete"] or value["unquantified_candidates"] or gaps:
                raise _error()
        elif value["neighborhood_complete"]:
            raise _error()
        if termination == "model_gap" and (value["quantification_complete"] or not value["unquantified_candidates"] or not gaps):
            raise _error()
    return {key: value[key] for key in OPTIMIZATION_FIELDS}


def postprocess_battle_axis(client, request: Mapping[str, Any], *, checkpoint: Callable[[], None] | None = None) -> dict[str, Any]:
    client._checkpoint(checkpoint)
    if not getattr(client, "supports_battle_axis_postprocess", False):
        raise NativeAnalysisError("战报轴装备优化组件未就绪，请更新配套分析组件")
    try:
        _validate_request(request)
        payload = json.dumps({"schema_version": REQUEST_SCHEMA, "batch_kind": BATCH_KIND,
            "dataset_version": client.dataset_version, "request": request}, ensure_ascii=False,
            allow_nan=False, separators=(",", ":")).encode("utf8")
        if len(payload) > MAX_BYTES:
            raise _error()
        request = json.loads(payload)["request"]
        _validate_request(request)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise _error() from None
    result = _json_object(client._run(payload, checkpoint=checkpoint))
    try:
        if (result.get("schema_version") != RESPONSE_SCHEMA or result.get("engine_version") != client.engine_version
                or result.get("dataset_version") != client.dataset_version or result.get("batch_kind") != BATCH_KIND
                or type(result.get("version")) is not int or result["version"] != 1
                or result.get("input_identity") != request["input_identity"] or result.get("model_id") != MODEL_ID
                or result.get("equipment_value_mode") != "actual_level" or result.get("critical_policy") != CRITICAL_POLICY
                or result.get("ordering_policy") != "previous_round_exact_delta_then_uid"
                or result.get("neighborhood") != "single_item_fixed_signature" or "error" in result
                or not isinstance(result.get("plans"), dict) or set(result["plans"]) != set(request["baseline_plans"])
                or any(not isinstance(plan, dict) for plan in result["plans"].values())):
            raise _error()
        if (result.get("optimization") or {}).get("termination") == "not_ready":
            if result["selected_result"] is not None:
                raise _error()
            if result.get("frozen_identity") is None:
                if result.get("scope") is not None or result.get("baseline") is not None:
                    raise _error()
                digest = result.get("source_input_digest")
                if digest is not None and (not isinstance(digest, str) or len(digest) != 64
                                          or any(c not in "0123456789abcdef" for c in digest)):
                    raise _error()
                facts = {"source_input_digest": digest, "frozen_identity": None, "scope": None, "baseline": None, "candidates": []}
            else:
                facts = decode_axis_response_facts({**result, "candidates": []}, request, ())
        else:
            facts = decode_axis_response_facts({**result, "candidates": [result["selected_result"]]}, request, ("selected",))
        optimization = _optimization(result.get("optimization"), request, facts)
        if optimization["termination"] == "not_ready" and result["plans"] != request["baseline_plans"]:
            raise _error()
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise _error() from None
    client._checkpoint(checkpoint)
    return {"schema_version": RESPONSE_SCHEMA, "engine_version": client.engine_version, "dataset_version": client.dataset_version,
        "batch_kind": BATCH_KIND, "version": 1, "input_identity": dict(request["input_identity"]), "model_id": MODEL_ID,
        "equipment_value_mode": "actual_level", "critical_policy": CRITICAL_POLICY,
        "source_input_digest": facts["source_input_digest"],
        "frozen_identity": facts["frozen_identity"], "scope": facts["scope"], "baseline": facts["baseline"],
        "selected_result": facts["candidates"][0] if facts["candidates"] else None, "optimization": optimization, "plans": result["plans"],
        "ordering_policy": result["ordering_policy"], "neighborhood": result["neighborhood"]}
