# 一次派发完整 Rust 伤害分配作业；只冻结数据、验收身份并恢复结果，不执行公式或搜索。
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import json
import math
from uuid import uuid4

from src.integrations.allocation_group_postprocess import _request, _response
from src.integrations.native_allocation import restore
from src.integrations.native_blueprints import DEFAULT_BUDGET, _verify_plans
from src.integrations.nte_analysis_core import MAX_BYTES, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, _json_object
from src.services.allocation.postprocess_facts import allocation_inputs_digest, freeze_postprocess_allocation_request


class DeferredDamageBlueprints:
    """编排阶段仅收集原值；真正生成与候选池上限统一留在 Rust 作业内。"""
    deferred = True

    def __init__(self):
        self.request = None

    def __call__(self, request):
        self.request = deepcopy({**request, "budget": request.get("budget") or DEFAULT_BUDGET})
        return {role["role"]: [] for role in request["roles"]}


@dataclass(frozen=True, slots=True)
class NativeDamageJob:
    inner: dict
    response: dict | None
    base_scores: dict
    elapsed_millis: int
    facts: dict


def _error():
    return NativeAnalysisError("完整伤害分配作业与冻结输入、图纸或结果身份不一致")


def _response_grace(client):
    # 留在原进程超时之内，只用于已完成结果的验收/编码/传输，不给搜索续时。
    return min(1_000, int(client.timeout * 1_000) // 10)


def execute_damage_job(client, request, scorer, frozen, blueprints, unit, *, axis=False,
                       max_evaluations=None, max_rounds=100, checkpoint=None):
    """返回生成图纸后的冻结请求及B0；正式优化服务只消费本作业已验收响应。"""
    from src.services.allocation.direct_projection import freeze_direct_projection_inputs
    client._checkpoint(checkpoint)
    if not getattr(client, "supports_damage_allocation_job", False):
        raise NativeAnalysisError("分析组件缺少完整 Rust 伤害分配能力，请更新配套组件")
    if blueprints.request is None:
        raise _error()
    allocation = freeze_postprocess_allocation_request(request, scorer)
    geometry = {row["shape_id"]: [(cell["x"], cell["y"]) for cell in row["cells"]]
                for row in unit.dao.list_shapes()}
    search_millis = max(1, min(120_000, int(client.timeout * 1_000) - _response_grace(client)))
    budget = {"max_evaluations": max_evaluations if max_evaluations is not None else 1_024 if axis else 20_000,
              "max_rounds": max_rounds, "max_millis": search_millis, "max_visits": 1_000_000}
    post = {"version": 2, "objective": "frozen_axis_expected_v1" if axis else "standard_direct_v1",
            "allocation_request": allocation, "baseline_plans": {}, "reserved_uids": list(frozen.reserved_uids),
            "role_character_ids": dict(frozen.role_character_ids), "budget": budget,
            "neighborhood": "single_pair_exchange_blueprint"}
    ready = not frozen.reason
    if axis:
        ready = ready and frozen.source is not None
        post.update(battle_request=json.loads(frozen.source.selector_json) if ready else {},
                    anchor_mode=frozen.selection.anchor_mode, equipment_value_mode="actual_level",
                    team_preflight=json.loads(frozen.selection.fixed_team_proof_json),
                    inventory_equipment_by_uid=json.loads(frozen.inventory_json), baseline_equipment_by_character=[])
    else:
        post["projection_inputs"] = (freeze_direct_projection_inputs(request, frozen.details, frozen.source_inventory,
            geometry_cells=geometry, checkpoint=checkpoint) if ready else {"details": {}, "inventory": {}, "geometry_cells": geometry})
    inner = {"version": 1, "run_postprocess": ready, "strategy": "axis_priority" if axis else "direct_priority",
             "allocation_request": allocation, "blueprint_request": deepcopy(blueprints.request),
             "postprocess_request": post, "geometry_cells": geometry, "max_millis": budget["max_millis"]}
    identity = {"request_id": str(uuid4()), "input_sha256": allocation_inputs_digest(inner)}
    inner["input_identity"] = post["input_identity"] = identity
    try:
        payload = json.dumps({"schema_version": REQUEST_SCHEMA, "dataset_version": client.dataset_version,
                              "batch_kind": "damage_allocation_v1", "request": inner},
                             ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
        if len(payload) > MAX_BYTES:
            raise _error()
        inner = json.loads(payload)["request"]
        value = _json_object(client._run(payload, checkpoint=checkpoint))
        client._checkpoint(checkpoint)
        return _decode(client, request, scorer, inner, value, axis=axis)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise _error() from None


def _decode(client, request, scorer, sent, value, *, axis):
    if freeze_postprocess_allocation_request(request, scorer) != sent["allocation_request"]:
        raise _error()
    if (value.get("schema_version") != RESPONSE_SCHEMA or value.get("engine_version") != client.engine_version
            or value.get("dataset_version") != client.dataset_version or value.get("batch_kind") != "damage_allocation_v1"
            or type(value.get("version")) is not int or value["version"] != 1
            or value.get("input_identity") != sent["input_identity"] or value.get("strategy") != sent["strategy"]
            or value.get("scoring_policy") != "equipment_base_score_v1" or value.get("job_policy") != "frozen_damage_job_v1"
            or type(value.get("elapsed_millis")) is not int
            or not 0 <= value["elapsed_millis"] < sent["max_millis"] + (_response_grace(client) if isinstance(value.get("result"), dict) else 0)):
        raise _error()
    generated = value["blueprints"]
    if generated.get("complete") is not True or generated.get("termination") != "complete":
        raise _error()
    _verify_plans(generated, sent["blueprint_request"])
    blueprint_budget = sent["blueprint_request"]["budget"]
    for field, limit in (("nodes", "max_nodes"), ("generated", "max_blueprints"),
                         ("combinations", "max_combinations"), ("output_bytes", "max_output_bytes")):
        if type(generated[field]) is not int or not 0 <= generated[field] <= blueprint_budget[limit]:
            raise _error()
    for field in ("drive_limit", "tape_limit"):
        if type(value[field]) is not int or not 1 <= value[field] <= 100_000:
            raise _error()
    effective = replace(request, blueprints_db=deepcopy(generated["blueprints"]),
                        drive_screen_limit=value["drive_limit"], tape_screen_limit=value["tape_limit"])
    baseline = restore(deepcopy(value["baseline_plans"]), effective)
    post = deepcopy(sent["postprocess_request"])
    for role in post["allocation_request"]["roles"]:
        role["blueprints"] = deepcopy(generated["blueprints"][role["name"]])
    for field in ("drive_limit", "tape_limit"):
        post["allocation_request"][field] = value[field]
    post["baseline_plans"] = deepcopy(value["baseline_plans"])
    elapsed_budget = value["postprocess_budget"]
    if (set(elapsed_budget) != set(post["budget"])
            or any(elapsed_budget[k] != post["budget"][k] for k in set(post["budget"]) - {"max_millis"})
            or type(elapsed_budget["max_millis"]) is not int or not 1 <= elapsed_budget["max_millis"] <= post["budget"]["max_millis"]):
        raise _error()
    post["budget"] = elapsed_budget
    if axis:
        post["baseline_equipment_by_character"] = value["baseline_equipment_by_character"]
    response = value["result"]
    if response is not None:
        expected = _request(post, axis=axis)
        response = _response(client, response, post, expected, axis=axis)
    scores = value["base_scores"]
    if not isinstance(scores, dict) or set(scores) != set(baseline):
        raise _error()
    grades = value["base_grades"]
    inventory = {item.uid: item for item in effective.inventory}
    if not isinstance(grades, dict) or set(grades) != set(scores):
        raise _error()
    facts = []
    for role in scores:
        if not isinstance(scores[role], dict) or not isinstance(grades[role], dict) or set(grades[role]) != set(scores[role]):
            raise _error()
        for uid, score in scores[role].items():
            if uid not in inventory or type(score) not in (int, float) or not math.isfinite(score):
                raise _error()
            facts.append((score, inventory[uid].area, grades[role][uid]))
    scorer._native_scores.accept_grades(facts)
    facts = {"job_policy": value["job_policy"], "scoring_policy": value["scoring_policy"],
             "job_elapsed_millis": value["elapsed_millis"],
             "job_budget": {"max_millis": sent["max_millis"], "response_grace_millis": _response_grace(client), "blueprints": deepcopy(blueprint_budget),
                            "search": deepcopy(sent["postprocess_request"]["budget"])},
             "blueprint_usage": {key: generated[key] for key in ("nodes", "generated", "combinations", "output_bytes")}}
    return effective, baseline, NativeDamageJob(post, response, scores, value["elapsed_millis"], facts)
