# 一次派发完整 Rust 伤害分配作业；只冻结数据、验收身份并恢复结果，不执行公式或搜索。
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import json
import math
from uuid import uuid4

from src.observability.stage_timings import timed_call, measure

from src.integrations.allocation.allocation_group_postprocess import _request, _response
from src.integrations.allocation.native_allocation import restore
from src.integrations.allocation.native_blueprints import DEFAULT_BUDGET, STOP_REASONS, _verify_plans
from src.integrations.analysis_core.nte_analysis_core import MAX_BYTES, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, _json_object
from src.services.allocation.results.postprocess_facts import allocation_inputs_digest, freeze_postprocess_allocation_request


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
                       max_evaluations=None, max_rounds=100, checkpoint=None, timings=None):
    """返回生成图纸后的冻结请求及B0；正式优化服务只消费本作业已验收响应。"""
    from src.services.allocation.direct.direct_projection import freeze_direct_projection_inputs
    client._checkpoint(checkpoint)
    if not getattr(client, "supports_damage_allocation_job", False):
        raise NativeAnalysisError("分析组件缺少完整 Rust 伤害分配能力，请更新配套组件")
    if blueprints.request is None:
        raise _error()
    if (any(role.get("suit_permanent_properties") for role in request.roles_db.values())
            and not getattr(client, "supports_allocation_suit_crit", False)):
        raise NativeAnalysisError("分析组件缺少完整 Rust 伤害分配能力，请更新配套组件")
    allocation = timed_call(timings, "allocation.damage.freeze", freeze_postprocess_allocation_request, request, scorer)
    geometry = {row["shape_id"]: [(cell["x"], cell["y"]) for cell in row["cells"]]
                for row in unit.dao.list_shapes()}
    from src.services.allocation.inputs.search_settings import search_budget
    profile = search_budget(frozen.selection.search_mode) if axis else {"max_millis": 120_000, "max_evaluations": 20_000}
    if axis and profile["max_millis"] > 120_000 and not getattr(client, "supports_axis_deep_search", False):
        raise NativeAnalysisError("深搜需要新版分析组件，请更新组件或选择快搜")
    search_millis = max(1, min(profile["max_millis"], int(client.timeout * 1_000) - _response_grace(client)))
    budget = {"max_evaluations": max_evaluations if max_evaluations is not None else profile["max_evaluations"],
              "max_rounds": max_rounds, "max_millis": search_millis, "max_visits": 1_000_000}
    post = {"version": 2, "objective": "frozen_axis_expected_v1" if axis else "standard_direct_v1",
            "allocation_request": allocation, "baseline_plans": {}, "reserved_uids": list(frozen.reserved_uids),
            "role_character_ids": dict(frozen.role_character_ids), "budget": budget,
            "neighborhood": "single_pair_exchange_blueprint"}
    ready = not frozen.reason
    if (not axis and ready and any(detail.get("suit_definitions") for _, detail in frozen.details)
            and not getattr(client, "supports_allocation_suit_permanent", False)):
        raise NativeAnalysisError("分析组件缺少完整 Rust 伤害分配能力，请更新配套组件")
    with measure(timings, "allocation.damage.model_inputs"):
        if axis:
            ready = ready and frozen.source is not None
            post.update(battle_request=json.loads(frozen.source.selector_json) if ready else {},
                        anchor_mode=frozen.selection.anchor_mode, equipment_value_mode="actual_level",
                        team_preflight=json.loads(frozen.selection.fixed_team_proof_json),
                        inventory_equipment_by_uid=json.loads(frozen.inventory_json), baseline_equipment_by_character=[])
            if frozen.additional_axes and ready:
                post["version"] = 3
                post["team_axes"] = [dict(character_ids=list(item.selection.selected_team_character_ids),
                    battle_request=json.loads(item.source.selector_json) if item.source else {},
                    anchor_mode=item.selection.anchor_mode, team_preflight=json.loads(item.selection.fixed_team_proof_json))
                    for item in (frozen, *frozen.additional_axes)]
        else:
            post["projection_inputs"] = (freeze_direct_projection_inputs(request, frozen.details, frozen.source_inventory,
                geometry_cells=geometry, checkpoint=checkpoint) if ready else {"details": {}, "inventory": {}, "geometry_cells": geometry})
    inner = {"version": 1, "run_postprocess": ready, "strategy": "axis_priority" if axis else "direct_priority",
             "allocation_request": allocation, "blueprint_request": deepcopy(blueprints.request),
             "postprocess_request": post, "geometry_cells": geometry, "max_millis": budget["max_millis"]}
    identity = {"request_id": str(uuid4()), "input_sha256": allocation_inputs_digest(inner)}
    inner["input_identity"] = post["input_identity"] = identity
    try:
        with measure(timings, "allocation.damage.encode"):
            payload = json.dumps({"schema_version": REQUEST_SCHEMA, "dataset_version": client.dataset_version,
                                  "batch_kind": "damage_allocation_v1", "request": inner},
                                 ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
            if len(payload) > MAX_BYTES:
                raise _error()
            inner = json.loads(payload)["request"]
        raw = timed_call(timings, "allocation.damage.component", client._run, payload, checkpoint=checkpoint)
        value = timed_call(timings, "allocation.damage.json_decode", _json_object, raw)
        client._checkpoint(checkpoint)
        stages = value.get("stage_timings")
        if stages is not None:
            expected = {"input_validation_millis", "blueprints_millis", "baseline_millis",
                        "postprocess_millis", "result_validation_millis", "total_millis"}
            if (not isinstance(stages, dict) or set(stages) != expected
                    or any(type(n) is not int or n < 0 for n in stages.values())):
                raise _error()
            if timings is not None:
                for key in sorted(expected - {"total_millis"}):
                    timings.record("allocation.damage.kernel." + key.removesuffix("_millis"), stages[key])
                nested = (value.get("result") or {}).get("stage_timings")
                if nested is not None:
                    keys = {"projection_ns", "replay_ns", "compare_ns", "evaluations"}
                    if (not isinstance(nested, list) or len(nested) > 2
                            or any(not isinstance(row, dict) or set(row) != keys
                                   or any(type(n) is not int or n < 0 for n in row.values()) for row in nested)):
                        raise _error()
                    for row in nested:
                        for key in ("projection_ns", "replay_ns", "compare_ns"):
                            timings.record("allocation.damage.kernel.axis_" + key.removesuffix("_ns"), row[key] / 1e6)
        return timed_call(timings, "allocation.damage.validate", _decode, client, request, scorer, inner, value, axis=axis)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise _error() from None


def _decode(client, request, scorer, sent, value, *, axis):
    # sent 已经过发送前的 JSON 往返；复核时以同一 JSON 规范形比较，
    # 整数键（如套装件数 4）与字符串键 "4" 视为同一冻结输入，真实取值变化仍会被拒绝。
    refrozen = json.loads(json.dumps(freeze_postprocess_allocation_request(request, scorer),
                                     ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    if refrozen != sent["allocation_request"]:
        raise _error()
    failure = value.get("postprocess_failure")
    if failure is not None and (not getattr(client, "supports_damage_baseline_fallback", False)
            or not isinstance(failure, dict) or set(failure) != {"status", "code", "baseline_complete"}
            or failure["status"] != "recoverable_failure" or failure["baseline_complete"] is not True
            or not isinstance(failure["code"], str)
            or failure["code"] not in {"damage_job_time_budget", "unsupported_standard_element",
                "direct_projection_growth_missing", "direct_projection_fork_missing", "group_postprocess_shape_bonus_missing"}
            or value.get("result") is not None):
        raise _error()
    if (value.get("schema_version") != RESPONSE_SCHEMA or value.get("engine_version") != client.engine_version
            or value.get("dataset_version") != client.dataset_version or value.get("batch_kind") != "damage_allocation_v1"
            or type(value.get("version")) is not int or value["version"] != 1
            or value.get("input_identity") != sent["input_identity"] or value.get("strategy") != sent["strategy"]
            or value.get("scoring_policy") != "equipment_base_score_v1" or value.get("job_policy") != "frozen_damage_job_v1"
            or type(value.get("elapsed_millis")) is not int
            or not 0 <= value["elapsed_millis"] < sent["max_millis"] + (_response_grace(client) if isinstance(value.get("result"), dict) or failure else 0)):
        raise _error()
    generated = value["blueprints"]
    if failure and generated.get("complete") is not True:
        raise _error()
    if (type(generated.get("complete")) is not bool
            or generated.get("termination") not in STOP_REASONS | {"complete"}
            or generated["complete"] != (generated["termination"] == "complete")):
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
    if failure and not all(plan.get("valid") is True and plan.get("assigned_tape") is not None for plan in baseline.values()):
        raise _error()
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
             "postprocess_failure": deepcopy(failure),
             "baseline_build_sha256": allocation_inputs_digest({"plans": value["baseline_plans"],
                 "equipment": value.get("baseline_equipment_by_character")}),
             "job_elapsed_millis": value["elapsed_millis"],
             "job_budget": {"max_millis": sent["max_millis"], "response_grace_millis": _response_grace(client), "blueprints": deepcopy(blueprint_budget),
                            "search": deepcopy(sent["postprocess_request"]["budget"])},
             "blueprint_usage": {key: generated[key] for key in ("nodes", "generated", "combinations", "output_bytes", "complete", "termination")}}
    return effective, baseline, NativeDamageJob(post, response, scores, value["elapsed_millis"], facts)
