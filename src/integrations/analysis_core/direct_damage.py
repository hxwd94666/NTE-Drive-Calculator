# 传输角色面板未取整直伤批次并验收完整乘区；不复制数值公式。
from __future__ import annotations

from dataclasses import fields
import hashlib
import json
import math
import time

from src.services.damage.damage_calculation_service import DirectDamageInput, DirectDamageResult


_INPUT_FIELDS = tuple(field.name for field in fields(DirectDamageInput))


def _wire_input(row):
    # DTO 仅含不可变标量/元组；显式取字段避免 asdict 为每个候选递归深拷贝，不改变字段或顺序。
    value = {name: getattr(row, name) for name in _INPUT_FIELDS}
    profile = row.enemy_defense_profile
    if profile is not None:
        value["enemy_defense_profile"] = {field.name: getattr(profile, field.name) for field in fields(profile)}
    return value


def calculate_direct_batch(client, inputs, *, checkpoint=None):
    from src.integrations.analysis_core.nte_analysis_core import (
        MAX_BYTES, MAX_JOBS, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, _json_object,
    )
    client._checkpoint(checkpoint)
    if not client.supports_direct_damage:
        raise NativeAnalysisError("独立分析核心响应版本或数据集不匹配")
    if not inputs:
        return ()
    if len(inputs) > MAX_JOBS:
        return tuple(row for start in range(0, len(inputs), MAX_JOBS)
                     for row in calculate_direct_batch(client, inputs[start:start + MAX_JOBS], checkpoint=checkpoint))
    start = time.perf_counter()
    wire = json.dumps({"schema_version": REQUEST_SCHEMA, "dataset_version": client.dataset_version,
                       "batch_kind": "direct_damage_v1", "inputs": [_wire_input(row) for row in inputs]},
                      allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode("utf8")
    if len(wire) > MAX_BYTES:
        if len(inputs) == 1:
            raise NativeAnalysisError("分析请求超过大小限制")
        middle = len(inputs) // 2
        return (calculate_direct_batch(client, inputs[:middle], checkpoint=checkpoint)
                + calculate_direct_batch(client, inputs[middle:], checkpoint=checkpoint))
    raw = client._run(wire, checkpoint=checkpoint)
    decode_start = time.perf_counter()
    response = _json_object(raw)
    decode_seconds = time.perf_counter() - decode_start
    validate_start = time.perf_counter()
    if (response.get("schema_version") != RESPONSE_SCHEMA or response.get("engine_version") != client.engine_version
            or response.get("dataset_version") != client.dataset_version or response.get("batch_kind") != "direct_damage_v1"
            or response.get("request_sha256") != hashlib.sha256(wire).hexdigest()
            or "error" in response or type(response.get("compute_elapsed_ns")) is not int
            or response["compute_elapsed_ns"] < 0 or not isinstance(response.get("results"), list)
            or len(response["results"]) != len(inputs)):
        raise NativeAnalysisError("独立分析核心响应版本或数据集不匹配")
    names = {field.name for field in fields(DirectDamageResult)}
    results = []
    for row in response["results"]:
        if (not isinstance(row, dict) or set(row) != names
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in row.values())):
            raise NativeAnalysisError("独立分析核心返回无效公式数值")
        results.append(DirectDamageResult(**row))
    client._checkpoint(checkpoint)
    client._record_timing("direct_damage_v1", started=start, jobs=len(inputs), input_bytes=len(wire),
                          output_bytes=len(raw), decode_seconds=decode_seconds,
                          validate_seconds=time.perf_counter() - validate_start,
                          kernel_seconds=response["compute_elapsed_ns"] / 1e9)
    return tuple(results)
