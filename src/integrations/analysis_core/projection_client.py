# 复用分析客户端的有界 Buff 投影协议与完整证据验收，不实现投影公式。
from __future__ import annotations
from collections.abc import Callable
import json
import time
from typing import Any
from src.domain.battle_report.analysis.backend_contracts import BuffProjectionBatchTooLarge
from src.integrations.battle_analysis.native_buff_projection_wire import expand_projection_tables

class ProjectionClientMixin:
    def project_batch(
        self, payload: dict[str, Any], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        return self._project_batch(payload, "buff_projection_v1", checkpoint=checkpoint)

    def project_plan_batch(
        self, payload: dict[str, Any], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
        if not self.supports_projection_plan:
            raise NativeAnalysisError("已部署的分析组件不支持批量候选投影")
        return self._project_batch(payload, "buff_projection_plan_v1", checkpoint=checkpoint)

    def _project_batch(
        self, payload: dict[str, Any], batch_kind: str, *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        from src.integrations.analysis_core.nte_analysis_core import MAX_JOBS, MAX_BYTES, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, _json_object
        jobs = payload["jobs"]
        identifiers = [row["job_id"] for row in jobs]
        if (len(jobs) > MAX_JOBS or len(set(identifiers)) != len(jobs)
                or any(not isinstance(key, str) or not key for key in identifiers)):
            raise ValueError("Buff 投影批次身份或大小无效")
        self._checkpoint(checkpoint)
        if not jobs:
            return ()
        started = time.perf_counter()
        encoding = "interned_v2" if self.supports_projection_evidence else "interned_v1"
        wire = json.dumps({
            **payload, "schema_version": REQUEST_SCHEMA, "dataset_version": self.dataset_version,
            "batch_kind": batch_kind, "result_encoding": encoding,
        }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(wire) > MAX_BYTES:
            raise BuffProjectionBatchTooLarge("Buff 投影请求超过大小限制")
        input_bytes = len(wire)
        raw = self._run(wire, checkpoint=checkpoint)
        del wire
        if len(raw) > MAX_BYTES:
            raise BuffProjectionBatchTooLarge("Buff 投影响应超过大小限制")
        output_bytes = len(raw)
        decode_started = time.perf_counter()
        response = _json_object(raw)
        decode_seconds = time.perf_counter() - decode_started
        validate_started = time.perf_counter()
        del raw
        if (response.get("schema_version") != RESPONSE_SCHEMA
                or response.get("engine_version") != self.engine_version
                or response.get("dataset_version") != self.dataset_version
                or response.get("batch_kind") != batch_kind or "error" in response):
            raise NativeAnalysisError("Buff 投影响应版本或数据集不匹配")
        if response.get("result_encoding") != encoding:
            raise NativeAnalysisError("Buff 投影响应编码不匹配")
        try:
            results = expand_projection_tables(response)
        except (KeyError, TypeError, ValueError) as error:
            raise NativeAnalysisError("Buff 投影响应共享证据表无效") from error
        kernel_ns = response.get("compute_elapsed_ns")
        encoding_ns = response.get("encoding_elapsed_ns", 0)
        evaluated_pairs = response.get("evaluated_interval_pairs", 0)
        del response
        if not isinstance(results, list) or len(results) != len(jobs):
            raise NativeAnalysisError("Buff 投影响应数量不匹配")
        projections = []
        validated_modifiers: set[int] = set()
        validated_decisions: set[int] = set()
        for index, row in enumerate(results):
            if not isinstance(row, dict) or row.get("job_id") != identifiers[index]:
                raise NativeAnalysisError("Buff 投影响应身份或顺序不匹配")
            projection = row.get("projection")
            self._validate_projection(projection, validated_modifiers, validated_decisions)
            if projection["event_id"] != payload["hits"][jobs[index]["hit_index"]]["event_id"]:
                raise NativeAnalysisError("Buff 投影响应逐击身份不匹配")
            projections.append(projection)
        if isinstance(kernel_ns, bool) or not isinstance(kernel_ns, int) or kernel_ns < 0:
            raise NativeAnalysisError("Buff 投影响应计时无效")
        if isinstance(encoding_ns, bool) or not isinstance(encoding_ns, int) or encoding_ns < 0:
            raise NativeAnalysisError("Buff 投影响应编码计时无效")
        if isinstance(evaluated_pairs, bool) or not isinstance(evaluated_pairs, int) or evaluated_pairs < 0:
            raise NativeAnalysisError("Buff 投影响应规则计数无效")
        with self._lock:
            self._stats["projection_jobs"] += len(jobs)
            self._stats["projection_batch_calls"] += 1
            self._stats["projection_duration_seconds"] += time.perf_counter() - started
            self._stats["projection_kernel_seconds"] += kernel_ns / 1_000_000_000
            self._stats["projection_input_bytes"] += input_bytes
            self._stats["projection_output_bytes"] += output_bytes
            self._stats["projection_encoding_seconds"] += encoding_ns / 1_000_000_000
            self._stats["projection_evaluated_interval_pairs"] += evaluated_pairs
        self._record_timing(batch_kind, started=started, jobs=len(jobs),
                            input_bytes=input_bytes, output_bytes=output_bytes,
                            decode_seconds=decode_seconds,
                            validate_seconds=time.perf_counter() - validate_started,
                            kernel_seconds=kernel_ns / 1_000_000_000)
        return tuple(projections)

    @classmethod
    def _validate_projection(cls, value: object, validated_modifiers: set[int] | None = None,
                             validated_decisions: set[int] | None = None) -> None:
        from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
        def strings(row: dict[str, Any], key: str) -> bool:
            values = row.get(key)
            return isinstance(values, list) and all(isinstance(item, str) for item in values)

        if not isinstance(value, dict) or not isinstance(value.get("event_id"), str):
            raise NativeAnalysisError("Buff 投影响应格式无效")
        if (not isinstance(value.get("confidence"), str)
                or value["confidence"] not in {"未解析", "低", "中", "高"}
                or any(not strings(value, key) for key in (
                    "applied_interval_ids", "excluded_interval_ids", "exclusion_reasons"))
                or not isinstance(value.get("modifiers"), list)
                or not isinstance(value.get("decisions"), list)):
            raise NativeAnalysisError("Buff 投影响应证据无效")
        for row in value["modifiers"]:
            if validated_modifiers is not None and id(row) in validated_modifiers:
                continue
            if (not isinstance(row, dict)
                    or any(not isinstance(row.get(key), str) for key in ("property_id", "target_scope"))
                    or not cls._finite_number(row.get("additive_value"))
                    or not isinstance(row.get("confidence"), str)
                    or row["confidence"] not in {"未解析", "低", "中", "高"}
                    or not strings(row, "interval_ids") or not strings(row, "buff_names")):
                raise NativeAnalysisError("Buff 投影响应属性无效")
            if validated_modifiers is not None:
                validated_modifiers.add(id(row))
        for row in value["decisions"]:
            if validated_decisions is not None and id(row) in validated_decisions:
                continue
            if (not isinstance(row, dict)
                    or any(not isinstance(row.get(key), str) for key in ("interval_id", "buff_name"))
                    or not isinstance(row.get("status"), str)
                    or row["status"] not in {"applied", "not_applied", "unresolved"}
                    or (row.get("observed_stacks") is not None and (
                        type(row["observed_stacks"]) is not int or row["observed_stacks"] < 0))
                    or not isinstance(row.get("state_confidence", ""), str)
                    or row.get("state_confidence", "") not in {"", "未知", "未解析", "低", "中", "高"}
                    or not strings(row, "applied_property_ids") or not strings(row, "reasons")):
                raise NativeAnalysisError("Buff 投影响应采用状态无效")
            if validated_decisions is not None:
                validated_decisions.add(id(row))
