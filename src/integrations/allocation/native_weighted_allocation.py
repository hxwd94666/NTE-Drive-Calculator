# 冻结加权配装协议并验证统一分配与单角色候选的装备归属。
from concurrent.futures import CancelledError
from dataclasses import replace
import hashlib
import json

from src.integrations.allocation.native_allocation import restore
from src.integrations.allocation.native_allocation_wire import freeze
from src.integrations.analysis_core.nte_analysis_core import (
    MAX_BYTES,
    REQUEST_SCHEMA,
    RESPONSE_SCHEMA,
    NativeAnalysisCancelled,
    NativeAnalysisError,
    _json_object,
)


CAPABILITY = "weighted_allocation_v1"


def execute_weighted_allocation(client, request, scorer, *, top_k, cancel_check=None):
    def checkpoint():
        if cancel_check is not None and cancel_check():
            raise CancelledError("配装计算已取消")

    checkpoint()
    wire = freeze(request, scorer, weighted=True)["request"]
    # 加权配置的暴击门槛使用装备、形状加成和基础暴击率。
    for role in wire["roles"]:
        role["data"].update(crit_source_known=True, non_equipment_crit_rate=0.0)
    encoded = json.dumps({
        "schema_version": REQUEST_SCHEMA,
        "dataset_version": client.dataset_version,
        "batch_kind": CAPABILITY,
        "request": {
            "allocation": wire,
            "property_limits": [
                [
                    {"stat_name": limit.stat_name, "minimum": limit.minimum, "maximum": limit.maximum}
                    for limit in request.property_limits.get(role["name"], ())
                ]
                for role in wire["roles"]
            ],
            "allow_missing_core": request.allow_missing_core,
            "top_k": top_k,
        },
    }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_BYTES:
        raise NativeAnalysisError("配装输入超过大小限制")
    try:
        result = _json_object(client._run(encoded, checkpoint=checkpoint))
    except NativeAnalysisCancelled:
        raise CancelledError("配装计算已取消") from None
    checkpoint()
    if (
        result.get("schema_version") != RESPONSE_SCHEMA
        or result.get("engine_version") != client.engine_version
        or result.get("dataset_version") != client.dataset_version
        or result.get("batch_kind") != CAPABILITY
        or type(result.get("version")) is not int
        or result["version"] != 1
        or result.get("request_sha256") != hashlib.sha256(encoded).hexdigest()
        or type(result.get("limited")) is not bool
    ):
        raise NativeAnalysisError("配装响应与冻结请求不一致")

    plans, role_top_k = result.get("plans"), result.get("role_top_k")
    if (
        not isinstance(plans, dict)
        or not isinstance(role_top_k, dict)
        or set(role_top_k) != (set(request.role_order) if top_k else set())
    ):
        raise NativeAnalysisError("配装响应角色不匹配")
    originals = {item.uid: item for item in request.inventory}
    restore(plans, request, frozen_models=True, originals=originals)
    if not request.allow_missing_core and any(p["valid"] and p.get("assigned_tape") is None for p in plans.values()):
        raise NativeAnalysisError("配装响应缺少必需核心")

    for role, batch in role_top_k.items():
        checkpoint()
        if (
            not isinstance(batch, dict)
            or not isinstance(batch.get("plans"), list)
            or len(batch["plans"]) > top_k
            or type(batch.get("limited")) is not bool
            or (batch.get("reason") is not None and not isinstance(batch["reason"], str))
        ):
            raise NativeAnalysisError("配装候选响应协议不匹配")
        single = replace(request, role_order=(role,))
        used = set()
        previous_score = float("inf")
        for plan in batch["plans"]:
            restore({role: plan}, single, frozen_models=True, originals=originals)
            if not plan["valid"] or (not request.allow_missing_core and plan.get("assigned_tape") is None):
                raise NativeAnalysisError("配装候选包含无效方案")
            items = [*plan.get("assigned_set_drives", ()), *plan.get("assigned_extra_drives", ())]
            if plan.get("assigned_tape") is not None:
                items.append(plan["assigned_tape"])
            uids = frozenset(item.uid for item in items)
            if not uids or uids in used or plan["score"] > previous_score:
                raise NativeAnalysisError("配装候选重复或排名不一致")
            used.add(uids)
            previous_score = plan["score"]

    checkpoint()
    return plans, role_top_k, result["limited"]
