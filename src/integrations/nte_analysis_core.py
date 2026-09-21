# 管理与采集 Core 完全隔离的 Rust 分析进程和批量公式协议。
"""Bounded, cancellable one-shot transport for the independent analysis binary."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
import math
from pathlib import Path
import subprocess
import threading
import time
from typing import Any
from src.integrations.native_buff_projection_wire import expand_projection_tables
from src.domain.native_analysis import BuffProjectionBatchTooLarge


REQUEST_SCHEMA = "nte-analysis-request-v1"
RESPONSE_SCHEMA = "nte-analysis-response-v1"
ENGINE_VERSION = "0.3.0"
SUPPORTED_ENGINE_VERSIONS = frozenset({"0.2.0", ENGINE_VERSION})
MAX_BYTES = 64 * 1024 * 1024
MAX_JOBS = 100_000


class NativeAnalysisError(RuntimeError):
    """An isolated analysis failure; never a capture-Core failure."""


class NativeAnalysisCancelled(NativeAnalysisError):
    """The frozen analysis request is no longer wanted."""


def _reject_constant(_value: str) -> None:
    raise NativeAnalysisError("分析核心返回了非有限 JSON 数值")


def _parse_finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise NativeAnalysisError("分析核心返回了非有限 JSON 数值")
    return number

def _json_object(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_BYTES:
        raise NativeAnalysisError("分析核心响应超过大小限制")
    try:
        value = json.loads(payload, parse_constant=_reject_constant, parse_float=_parse_finite_float)
    except (UnicodeError, ValueError) as error:
        raise NativeAnalysisError("分析核心返回无效 JSON") from error
    if not isinstance(value, dict):
        raise NativeAnalysisError("分析核心响应必须是对象")
    return value


class NteAnalysisCoreClient:
    """Own only short-lived analysis children; never connect to nte-core."""

    def __init__(
        self,
        executable: Path,
        dataset_version: str,
        timeout: float = 120.0,
        cancelled: Callable[[], bool] | None = None,
        *,
        engine_version: str = ENGINE_VERSION,
        capabilities: frozenset[str] | None = None,
    ) -> None:
        self.executable = Path(executable).resolve()
        if self.executable.name.casefold() != "nte-analysis-core.exe":
            raise ValueError("分析后端必须使用独立的 nte-analysis-core.exe")
        if not self.executable.is_file():
            raise NativeAnalysisError("缺少独立分析核心，请重新部署分析组件")
        if not dataset_version.strip() or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("分析数据集版本和有效超时必填")
        self.dataset_version = dataset_version
        if engine_version not in SUPPORTED_ENGINE_VERSIONS:
            raise ValueError("分析核心版本不受支持")
        self.engine_version = engine_version
        self.supports_projection_plan = (
            engine_version == ENGINE_VERSION
            and (capabilities is None or "buff_projection_plan_v1" in capabilities)
        )
        self.supports_battle_compute = (
            engine_version == ENGINE_VERSION
            and (capabilities is None or "battle_compute_v1" in capabilities)
        )
        self.supports_battle_page = (
            engine_version == ENGINE_VERSION
            and capabilities is not None and "battle_page_v1" in capabilities
        )
        self.supports_battle_progress = (
            self.supports_battle_page
            and capabilities is not None and "battle_progress_v1" in capabilities
        )
        self.supports_projection_evidence = (
            capabilities is not None and "interned_buff_projection_v2" in capabilities
        )
        self.supports_battle_page_identity = (
            self.supports_battle_page
            and capabilities is not None and "battle_page_identity_v1" in capabilities
        )
        self.supports_topple_composition = (
            self.supports_battle_page
            and capabilities is not None and "battle_topple_composition_v1" in capabilities
        )
        self.timeout = timeout
        self.cancelled = cancelled
        self._lock = threading.Lock()
        self._stats: dict[str, int | float] = {
            "jobs": 0, "batch_calls": 0, "duration_seconds": 0.0,
            "kernel_seconds": 0.0, "input_bytes": 0, "output_bytes": 0,
            "projection_jobs": 0, "projection_batch_calls": 0,
            "projection_duration_seconds": 0.0, "projection_kernel_seconds": 0.0,
            "projection_input_bytes": 0, "projection_output_bytes": 0,
            "projection_encoding_seconds": 0.0,
            "projection_evaluated_interval_pairs": 0,
            "compute_jobs": 0, "compute_batch_calls": 0, "compute_duration_seconds": 0.0,
            "compute_kernel_seconds": 0.0, "compute_input_bytes": 0, "compute_output_bytes": 0,
            "page_calls": 0, "page_seconds": 0.0, "page_kernel_seconds": 0.0,
            "page_input_bytes": 0, "page_output_bytes": 0,
        }

    @classmethod
    def from_executable(
        cls, executable: Path, dataset_version: str,
        *, timeout: float = 120.0, cancelled: Callable[[], bool] | None = None,
    ) -> NteAnalysisCoreClient:
        """Bind diagnostic tools to the explicitly selected binary's identity."""
        probe = cls(executable, dataset_version, timeout, cancelled)
        identity = _json_object(probe._run(None, version=True))
        capabilities = identity.get("capabilities")
        if (identity.get("engine") != "nte-analysis-core"
                or not isinstance(identity.get("engine_version"), str)
                or identity.get("engine_version") not in SUPPORTED_ENGINE_VERSIONS
                or identity.get("schema_version") != RESPONSE_SCHEMA
                or identity.get("request_schema_version") != REQUEST_SCHEMA
                or not isinstance(capabilities, list)
                or any(not isinstance(item, str) for item in capabilities)):
            raise NativeAnalysisError("独立分析核心身份、版本或能力无效")
        return cls(executable, dataset_version, timeout, cancelled,
                   engine_version=identity["engine_version"], capabilities=frozenset(capabilities))

    @property
    def stats(self) -> dict[str, int | float]:
        with self._lock:
            return dict(self._stats)

    def _checkpoint(self, checkpoint: Callable[[], None] | None) -> None:
        if self.cancelled is not None and self.cancelled():
            raise NativeAnalysisCancelled("分析请求已取消或账号上下文已失效")
        if checkpoint is not None:
            checkpoint()

    def _run(
        self, payload: bytes | None, *, version: bool = False,
        checkpoint: Callable[[], None] | None = None,
    ) -> bytes:
        self._checkpoint(checkpoint)
        args = [str(self.executable)]
        if version:
            args.append("--version-json")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=flags,
            )
        except OSError as error:
            raise NativeAnalysisError("独立分析核心无法启动") from error
        deadline = time.monotonic() + self.timeout
        pending_input = payload
        try:
            while True:
                self._checkpoint(checkpoint)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise NativeAnalysisError("独立分析核心计算超时")
                try:
                    stdout, _stderr = process.communicate(
                        input=pending_input, timeout=min(0.1, remaining),
                    )
                    break
                except subprocess.TimeoutExpired:
                    pending_input = None
            self._checkpoint(checkpoint)
            self._check_exit(process.returncode, stdout)
            return stdout
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()

    def _check_exit(self, returncode: int, stdout: bytes) -> None:
        if returncode != 0:
            if returncode == 2 and len(stdout) <= MAX_BYTES:
                try:
                    rejected = _json_object(stdout)
                except NativeAnalysisError:
                    rejected = {}
                error = rejected.get("error")
                if (rejected.get("engine_version") == self.engine_version
                        and rejected.get("schema_version") == RESPONSE_SCHEMA
                        and isinstance(error, dict)
                        and error.get("code") == "projection_plan_too_large"):
                    raise BuffProjectionBatchTooLarge("Buff 候选计划超过核心工作量限制")
            # Do not surface raw stderr, request JSON or private values.
            raise NativeAnalysisError(
                f"独立分析核心拒绝请求或执行失败（退出码 {returncode}）"
            )

    def _run_progress(
        self, payload: bytes, *, checkpoint: Callable[[], None] | None,
        progress_callback: Callable[[Mapping[str, Any]], None],
    ) -> bytes:
        from src.integrations.native_analysis_stream import (
            NativeStreamError, communicate_progress,
        )
        self._checkpoint(checkpoint)
        try:
            process = subprocess.Popen(
                [str(self.executable), "--progress"], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            raise NativeAnalysisError("独立分析核心无法启动") from error
        try:
            stdout, returncode = communicate_progress(
                process, payload, timeout=self.timeout, max_output_bytes=MAX_BYTES,
                checkpoint=lambda: self._checkpoint(checkpoint),
                progress_callback=progress_callback,
            )
        except NativeStreamError as error:
            raise NativeAnalysisError(str(error)) from None
        self._check_exit(returncode, stdout)
        return stdout

    def version(self) -> dict[str, Any]:
        value = _json_object(self._run(None, version=True))
        if value.get("engine") != "nte-analysis-core":
            raise NativeAnalysisError("独立分析核心身份不匹配")
        if value.get("engine_version") != self.engine_version:
            raise NativeAnalysisError("独立分析核心版本不匹配")
        if self.supports_projection_plan and "buff_projection_plan_v1" not in value.get("capabilities", []):
            raise NativeAnalysisError("独立分析核心缺少批量候选投影能力")
        if self.supports_projection_evidence and "interned_buff_projection_v2" not in value.get("capabilities", []):
            raise NativeAnalysisError("独立分析核心缺少逐击状态证据投影能力")
        if self.supports_battle_compute and "battle_compute_v1" not in value.get("capabilities", []):
            raise NativeAnalysisError("独立分析核心缺少扩展战报计算能力")
        if self.supports_battle_progress and "battle_progress_v1" not in value.get("capabilities", []):
            raise NativeAnalysisError("独立分析核心缺少战报进度能力")
        if self.supports_battle_page_identity and "battle_page_identity_v1" not in value.get("capabilities", []):
            raise NativeAnalysisError("独立分析核心缺少战报输入身份能力")
        return value

    def compute_batch(
        self, operation: str, inputs: Sequence[dict[str, Any]], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        from src.integrations.native_battle_compute import compute_battle_batch
        return compute_battle_batch(self, operation, inputs, checkpoint=checkpoint)

    def load_battle_page_identity(
        self, request: Mapping[str, Any], *, checkpoint: Callable[[], None] | None = None,
    ) -> str:
        """Probe frozen database inputs without sending candidates or cached results."""
        if not self.supports_battle_page_identity:
            raise NativeAnalysisError("独立分析核心不支持战报输入身份核对")
        probe = {key: request[key] for key in (
            "account_id", "generation", "battle_record_id", "user_database_path",
            "static_database_path", "semantics_path",
        )}
        probe["detail_level"] = "identity"
        result = self.load_battle_page(probe, checkpoint=checkpoint)
        digest = result.get("input_digest")
        if (set(result) != {"input_digest"} or not isinstance(digest, str)
                or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
            raise NativeAnalysisError("独立分析核心战报输入身份无效")
        return digest

    def load_battle_page(
        self, request: Mapping[str, Any], *,
        checkpoint: Callable[[], None] | None = None,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Send selectors and user edits; the native process reads report data."""
        if not self.supports_battle_page:
            raise NativeAnalysisError("分析组件尚不支持数据库直读，请部署对应版本")
        started = time.perf_counter()
        payload = json.dumps({
            "schema_version": REQUEST_SCHEMA, "batch_kind": "battle_page_v1",
            "dataset_version": self.dataset_version, "request": dict(request),
        }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_BYTES:
            raise NativeAnalysisError("战报页面输入超过大小限制")
        if self.supports_battle_progress and progress_callback is not None:
            raw = self._run_progress(
                payload, checkpoint=checkpoint, progress_callback=progress_callback,
            )
        else:
            raw = self._run(payload, checkpoint=checkpoint)
        response = _json_object(raw)
        if (response.get("schema_version") != RESPONSE_SCHEMA
                or response.get("engine_version") != self.engine_version
                or response.get("batch_kind") != "battle_page_v1"
                or response.get("dataset_version") != self.dataset_version
                or any(response.get(key) != request.get(key) for key in
                       ("account_id", "generation", "battle_record_id"))
                or not isinstance(response.get("result"), dict)):
            raise NativeAnalysisError("战报页面响应冻结身份不匹配")
        kernel_ns = response.get("compute_elapsed_ns")
        if type(kernel_ns) is not int or kernel_ns < 0:
            raise NativeAnalysisError("战报页面响应计时无效")
        with self._lock:
            self._stats["page_calls"] += 1
            self._stats["page_seconds"] += time.perf_counter() - started
            self._stats["page_kernel_seconds"] += kernel_ns / 1_000_000_000
            self._stats["page_input_bytes"] += len(payload)
            self._stats["page_output_bytes"] += len(raw)
        self._checkpoint(checkpoint)
        return response["result"]

    def evaluate(
        self, jobs: Sequence[Mapping[str, Any]], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if len(jobs) > MAX_JOBS:
            raise ValueError("分析批次命中数超过限制")
        identifiers = [row["job_id"] for row in jobs]
        if any(not isinstance(value, str) or not value for value in identifiers):
            raise ValueError("分析作业 ID 必须是非空字符串")
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("分析作业 ID 重复")
        started = time.perf_counter()
        payload = json.dumps({
            "schema_version": REQUEST_SCHEMA,
            "dataset_version": self.dataset_version,
            "jobs": list(jobs),
        }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_BYTES:
            raise ValueError("分析请求超过大小限制")
        raw = self._run(payload, checkpoint=checkpoint)
        response = _json_object(raw)
        if (
            response.get("schema_version") != RESPONSE_SCHEMA
            or response.get("engine_version") != self.engine_version
            or response.get("dataset_version") != self.dataset_version
            or "error" in response
        ):
            raise NativeAnalysisError("独立分析核心响应版本或数据集不匹配")
        results = response.get("results")
        if not isinstance(results, list) or len(results) != len(jobs):
            raise NativeAnalysisError("独立分析核心响应作业数量不匹配")
        for index, row in enumerate(results):
            if not isinstance(row, dict) or row.get("job_id") != identifiers[index]:
                raise NativeAnalysisError("独立分析核心响应作业顺序或身份不匹配")
            self._validate_numeric(row.get("original"))
            if jobs[index].get("candidate") is not None:
                self._validate_numeric(row.get("candidate"))
            elif row.get("candidate") is not None:
                raise NativeAnalysisError("独立分析核心返回了未请求的候选")
            ratio = row.get("ratio")
            state = row.get("ratio_status")
            if state not in {"complete", "unavailable", "not_requested"}:
                raise NativeAnalysisError("独立分析核心返回无效比值状态")
            if state == "complete":
                if not self._finite_number(ratio) or ratio < 0:
                    raise NativeAnalysisError("独立分析核心返回无效比值")
            elif ratio is not None:
                raise NativeAnalysisError("未量化结果不能携带比值")
        kernel_ns = response.get("compute_elapsed_ns", 0)
        if not isinstance(kernel_ns, int) or kernel_ns < 0:
            raise NativeAnalysisError("独立分析核心返回无效计时")
        with self._lock:
            self._stats["jobs"] += len(jobs)
            self._stats["batch_calls"] += 1
            self._stats["duration_seconds"] += time.perf_counter() - started
            self._stats["kernel_seconds"] += kernel_ns / 1_000_000_000
            self._stats["input_bytes"] += len(payload)
            self._stats["output_bytes"] += len(raw)
        return response

    @staticmethod
    def _finite_number(value: object) -> bool:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)

    @classmethod
    def _validate_numeric(cls, row: object) -> None:
        if not isinstance(row, dict) or row.get("status") not in {"complete", "partial", "unavailable"}:
            raise NativeAnalysisError("独立分析核心返回无效公式状态")
        if not isinstance(row.get("gap_codes"), list):
            raise NativeAnalysisError("独立分析核心响应缺少证据缺口")
        fields = ("raw_non_critical", "non_critical_damage", "critical_damage", "critical_rate", "expected_damage")
        for key in fields:
            if key not in row or (row[key] is not None and not cls._finite_number(row[key])):
                raise NativeAnalysisError("独立分析核心返回无效公式数值")
        if row["status"] == "unavailable":
            if any(row[key] is not None for key in fields) or not row["gap_codes"]:
                raise NativeAnalysisError("不可计算公式不得携带伤害")
        elif row["non_critical_damage"] is None or not isinstance(row.get("factors"), dict):
            raise NativeAnalysisError("可计算公式缺少数值或乘区")

    def calculate_batch(
        self, inputs: Sequence[dict[str, Any]], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        if not inputs:
            self._checkpoint(checkpoint)
            return ()
        jobs = [{"job_id": str(index), "original": value, "candidate": None}
                for index, value in enumerate(inputs)]
        response = self.evaluate(jobs, checkpoint=checkpoint)
        return tuple(row["original"] for row in response["results"])

    def project_batch(
        self, payload: dict[str, Any], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        return self._project_batch(payload, "buff_projection_v1", checkpoint=checkpoint)

    def project_plan_batch(
        self, payload: dict[str, Any], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        if not self.supports_projection_plan:
            raise NativeAnalysisError("已部署的分析组件不支持批量候选投影")
        return self._project_batch(payload, "buff_projection_plan_v1", checkpoint=checkpoint)

    def _project_batch(
        self, payload: dict[str, Any], batch_kind: str, *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]:
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
        response = _json_object(raw)
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
        return tuple(projections)

    @classmethod
    def _validate_projection(cls, value: object, validated_modifiers: set[int] | None = None,
                             validated_decisions: set[int] | None = None) -> None:
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
