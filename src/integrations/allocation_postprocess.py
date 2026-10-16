# 校验独立配装后处理协议与冻结目标身份，不在适配层计算伤害或搜索。
from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
from typing import Any

from src.integrations.nte_analysis_core import (
    MAX_BYTES, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, _json_object,
)

OPTIMIZATION_FIELDS = frozenset({
    "objective", "model_id", "selected_role", "baseline_value", "candidate_value", "delta",
    "evaluations", "rounds", "improvements", "termination", "reason", "neighborhood_complete",
})


def _identity(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != {"request_id", "input_sha256"}:
        return False
    digest = value["input_sha256"]
    return (isinstance(value["request_id"], str) and bool(value["request_id"].strip())
            and isinstance(digest, str) and len(digest) == 64
            and all(char in "0123456789abcdef" for char in digest))


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0.0


def _validate_optimization(value: Any, request: Mapping[str, Any]) -> None:
    if not isinstance(value, dict) or not OPTIMIZATION_FIELDS <= value.keys():
        raise NativeAnalysisError("直伤后处理响应缺少目标事实")
    if (value["objective"] != request["objective"] or value["selected_role"] != request["selected_role"]
            or value["model_id"] != request["role_model"]["model_id"]
            or not isinstance(value["reason"], str) or type(value["neighborhood_complete"]) is not bool):
        raise NativeAnalysisError("直伤后处理目标身份不匹配")
    for key, limit in (("evaluations", request["budget"]["max_evaluations"]),
                       ("rounds", request["budget"]["max_rounds"]),
                       ("improvements", request["budget"]["max_rounds"])):
        if type(value[key]) is not int or not 0 <= value[key] <= limit:
            raise NativeAnalysisError("直伤后处理预算事实无效")
    termination = value["termination"]
    if termination == "not_ready":
        if any(value[key] is not None for key in ("baseline_value", "candidate_value", "delta")):
            raise NativeAnalysisError("未就绪的直伤目标包含伪造数值")
        if value["improvements"] or value["neighborhood_complete"]:
            raise NativeAnalysisError("未就绪的直伤目标被标为完成")
        return
    if termination not in {"local_optimum", "evaluation_budget", "round_budget"}:
        raise NativeAnalysisError("直伤后处理终止状态无效")
    baseline, candidate, delta = (value[key] for key in ("baseline_value", "candidate_value", "delta"))
    if (not all(_number(number) for number in (baseline, candidate, delta))
            or candidate < baseline or not math.isclose(candidate - baseline, delta, rel_tol=1e-9, abs_tol=1e-7)
            or value["evaluations"] < 1
            or value["improvements"] > value["rounds"]
            or (termination == "local_optimum" and not value["neighborhood_complete"])
            or (termination in {"evaluation_budget", "round_budget"} and value["neighborhood_complete"])):
        raise NativeAnalysisError("直伤后处理收益或完整邻域事实无效")


def postprocess_allocation(
    client, request: Mapping[str, Any], *, checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """One frozen request owns one child; the existing transport owns cancellation and reap."""
    client._checkpoint(checkpoint)
    if not getattr(client, "supports_allocation_postprocess", False):
        raise NativeAnalysisError("基础配装完成，直伤优化组件未就绪，请更新配套分析组件")
    try:
        budget = request["budget"]
        if (type(request["version"]) is not int or request["version"] != 1
                or request["objective"] != "standard_direct_v1"
                or not isinstance(request["selected_role"], str) or not request["selected_role"]
                or not _identity(request["input_identity"])
                or not isinstance(request["role_model"]["model_id"], str)
                or type(budget["max_evaluations"]) is not int or not 1 <= budget["max_evaluations"] <= 100_000
                or type(budget["max_rounds"]) is not int or not 1 <= budget["max_rounds"] <= 1_000):
            raise ValueError
        payload = json.dumps({"schema_version": REQUEST_SCHEMA, "batch_kind": "allocation_postprocess_v1",
                              "dataset_version": client.dataset_version, "request": request},
                             ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (KeyError, TypeError, ValueError):
        raise NativeAnalysisError("直伤后处理冻结请求无效") from None
    if len(payload) > MAX_BYTES:
        raise NativeAnalysisError("直伤后处理输入超过大小限制")
    result = _json_object(client._run(payload, checkpoint=checkpoint))
    if (result.get("schema_version") != RESPONSE_SCHEMA or result.get("engine_version") != client.engine_version
            or result.get("dataset_version") != client.dataset_version
            or result.get("batch_kind") != "allocation_postprocess_v1"
            or type(result.get("version")) is not int or result["version"] != 1
            or result.get("input_identity") != request["input_identity"]
            or not isinstance(result.get("plans"), dict)
            or any(not isinstance(plan, dict) for plan in result.get("plans", {}).values())):
        raise NativeAnalysisError("直伤后处理响应冻结身份不匹配")
    _validate_optimization(result.get("optimization"), request)
    # 协议可以扩展外层，但未校验的附加属性不进入账号持久化或展示。
    result["optimization"] = {key: result["optimization"][key] for key in OPTIMIZATION_FIELDS}
    client._checkpoint(checkpoint)
    return result
