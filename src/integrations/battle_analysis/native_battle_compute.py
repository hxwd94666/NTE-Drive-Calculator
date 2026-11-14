# 传输冻结战报计算批次并校验原生结果身份，不包含业务公式。
from __future__ import annotations

from collections.abc import Callable, Sequence
import json
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.integrations.analysis_core.nte_analysis_core import NteAnalysisCoreClient


def compute_battle_batch(
    client: NteAnalysisCoreClient, operation: str, inputs: Sequence[dict[str, Any]],
    *, checkpoint: Callable[[], None] | None = None,
) -> tuple[dict[str, Any], ...]:
    from src.integrations.analysis_core.nte_analysis_core import (
        MAX_BYTES, MAX_JOBS, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, _json_object,
    )

    if not client.supports_battle_compute:
        raise NativeAnalysisError("已部署的分析组件不支持扩展战报计算")
    if not isinstance(operation, str) or not operation or len(operation) > 80:
        raise ValueError("战报计算操作无效")
    client._checkpoint(checkpoint)
    if not inputs:
        return ()
    if len(inputs) > MAX_JOBS:
        return tuple(row for start in range(0, len(inputs), MAX_JOBS)
                     for row in compute_battle_batch(client, operation, inputs[start:start + MAX_JOBS],
                                                    checkpoint=checkpoint))
    started = time.perf_counter()
    payload = json.dumps({
        "schema_version": REQUEST_SCHEMA, "dataset_version": client.dataset_version,
        "batch_kind": "battle_compute_v1", "operation": operation,
        "jobs": [{"job_id": str(index), "input": row} for index, row in enumerate(inputs)],
    }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_BYTES:
        del payload
        if len(inputs) <= 1:
            raise NativeAnalysisError("单项战报计算输入超过大小限制")
        midpoint = len(inputs) // 2
        return (compute_battle_batch(client, operation, inputs[:midpoint], checkpoint=checkpoint)
                + compute_battle_batch(client, operation, inputs[midpoint:], checkpoint=checkpoint))
    input_bytes = len(payload)
    raw = client._run(payload, checkpoint=checkpoint)
    decode_started = time.perf_counter()
    response = _json_object(raw)
    decode_seconds = time.perf_counter() - decode_started
    validate_started = time.perf_counter()
    if (response.get("schema_version") != RESPONSE_SCHEMA
            or response.get("engine_version") != client.engine_version
            or response.get("dataset_version") != client.dataset_version
            or response.get("batch_kind") != "battle_compute_v1"
            or response.get("operation") != operation or "error" in response):
        raise NativeAnalysisError("战报计算响应版本或操作不匹配")
    rows = response.get("results")
    if not isinstance(rows, list) or len(rows) != len(inputs):
        raise NativeAnalysisError("战报计算响应数量不匹配")
    values = []
    for index, row in enumerate(rows):
        if (not isinstance(row, dict) or row.get("job_id") != str(index)
                or not isinstance(row.get("value"), dict)):
            raise NativeAnalysisError("战报计算响应身份或格式不匹配")
        values.append(row["value"])
    kernel_ns = response.get("compute_elapsed_ns")
    if isinstance(kernel_ns, bool) or not isinstance(kernel_ns, int) or kernel_ns < 0:
        raise NativeAnalysisError("战报计算响应计时无效")
    client._checkpoint(checkpoint)
    with client._lock:
        client._stats["compute_batch_calls"] += 1
        client._stats["compute_jobs"] += len(inputs)
        client._stats["compute_duration_seconds"] += time.perf_counter() - started
        client._stats["compute_kernel_seconds"] += kernel_ns / 1_000_000_000
        client._stats["compute_input_bytes"] += input_bytes
        client._stats["compute_output_bytes"] += len(raw)
    client._record_timing(operation, started=started, jobs=len(inputs),
                          input_bytes=input_bytes, output_bytes=len(raw),
                          decode_seconds=decode_seconds,
                          validate_seconds=time.perf_counter() - validate_started,
                          kernel_seconds=kernel_ns / 1_000_000_000)
    return tuple(values)
