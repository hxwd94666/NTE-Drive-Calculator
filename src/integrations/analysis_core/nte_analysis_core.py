# 管理与采集 Core 完全隔离的 Rust 分析进程和批量公式协议。
"""Bounded, cancellable one-shot transport for the independent analysis binary."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import subprocess
import threading
import time
from typing import Any
from src.integrations.analysis_core.component_lease import ComponentLeaseMixin
from src.integrations.analysis_core.worker_session import WorkerSessionMixin
from src.integrations.analysis_core.projection_client import ProjectionClientMixin
from src.domain.battle_report.analysis.backend_contracts import BuffProjectionBatchTooLarge


REQUEST_SCHEMA = "nte-analysis-request-v1"
RESPONSE_SCHEMA = "nte-analysis-response-v1"
ENGINE_VERSION = "0.3.15"
CURRENT_PROTOCOL_ENGINE_VERSIONS = frozenset({"0.3.0", "0.3.1", "0.3.2", "0.3.3", "0.3.4", "0.3.5", "0.3.6", "0.3.7", "0.3.8", "0.3.9", "0.3.10", "0.3.11", "0.3.12", "0.3.13", "0.3.14", ENGINE_VERSION})
SUPPORTED_ENGINE_VERSIONS = CURRENT_PROTOCOL_ENGINE_VERSIONS | {"0.2.0"}
MAX_BYTES = 64 * 1024 * 1024
MAX_JOBS = 100_000


class NativeAnalysisError(RuntimeError):
    """An isolated analysis failure; never a capture-Core failure."""


class NativeAnalysisCancelled(NativeAnalysisError):
    """The frozen analysis request is no longer wanted."""


@dataclass(frozen=True, slots=True)
class AnalysisComponentIdentity:
    """发行绑定只认可创建时核验的执行物和能力清单，不接纳会话中途替换。"""

    executable: Path
    manifest_path: Path | None
    executable_sha256: str
    manifest_sha256: str

    def verify(self, checkpoint: Callable[[], None]) -> None:
        # 每次派发前按块核对字节并响应取消；路径检查不声称消除检查至 Popen 的竞态。
        for path, expected in ((self.manifest_path, self.manifest_sha256),
                               (self.executable, self.executable_sha256)):
            if path is None:
                continue  # 显式诊断候选没有发行清单，但执行物仍绑定冻结哈希。
            checkpoint()
            digest = hashlib.sha256()
            try:
                with path.open('rb') as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        checkpoint()
            except OSError as error:
                raise NativeAnalysisError('独立分析组件读取失败，请重新部署完整组件并重启应用') from error
            if digest.hexdigest() != expected:
                raise NativeAnalysisError('独立分析组件版本或哈希已变更，请核对完整组件并重启应用')
            checkpoint()


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


class NteAnalysisCoreClient(ComponentLeaseMixin, WorkerSessionMixin, ProjectionClientMixin):
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
        component_identity: AnalysisComponentIdentity | None = None,
    ) -> None:
        self._init_component_lease()
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
        self._capabilities = frozenset(capabilities or ())
        self.supports_projection_plan = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS
            and (capabilities is None or "buff_projection_plan_v1" in self._capabilities)
        )
        self.supports_battle_compute = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS
            and (capabilities is None or "battle_compute_v1" in self._capabilities)
        )
        self.supports_battle_page = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS
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
        self.supports_allocation = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS
            and capabilities is not None and "allocation_v2" in capabilities
        )
        self.supports_allocation_suit_permanent = (
            self.supports_allocation and capabilities is not None and "allocation_suit_permanent_v1" in capabilities
        )
        self.supports_allocation_suit_crit = capabilities is not None and "allocation_suit_crit_v1" in capabilities
        self.supports_damage_baseline_fallback = capabilities is not None and "damage_allocation_baseline_fallback_v1" in capabilities
        self.supports_axis_deep_search = capabilities is not None and "battle_axis_deep_search_v1" in capabilities
        self.supports_damage_allocation_job = (
            self.supports_allocation and capabilities is not None and "damage_allocation_v1" in capabilities
        )
        self.supports_equipment_scoring = (
            self.supports_allocation and capabilities is not None and "equipment_scoring_v1" in capabilities
        )
        self.supports_blueprints = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS
            and capabilities is not None and "allocation_blueprints_v1" in capabilities
        )
        self.supports_allocation_group_postprocess = (
            self.supports_allocation and capabilities is not None and "allocation_group_postprocess_v1" in capabilities
        )
        self.supports_allocation_group_projection = (
            self.supports_allocation_group_postprocess and capabilities is not None
            and "allocation_group_postprocess_v2" in capabilities
        )
        self.supports_battle_axis_group_postprocess = (
            self.supports_allocation and capabilities is not None and "battle_axis_group_postprocess_v1" in capabilities
        )
        self.supports_battle_axis_group_rematch = (
            self.supports_battle_axis_group_postprocess and capabilities is not None
            and "battle_axis_group_postprocess_v2" in capabilities
        )
        self.supports_battle_axis_joint_teams = (
            self.supports_battle_axis_group_rematch and "battle_axis_group_postprocess_v3" in capabilities
        )
        self.supports_allocation_postprocess = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS
            and capabilities is not None and "allocation_postprocess_v1" in capabilities
        )
        self.supports_battle_axis_team = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS
            and capabilities is not None and "battle_axis_team_v1" in capabilities
        )
        self.supports_battle_axis_fixed_team = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS
            and capabilities is not None and "battle_axis_fixed_team_v1" in capabilities
        )
        self.supports_battle_axis_evaluation = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS
            and capabilities is not None and "battle_axis_evaluate_v1" in capabilities
        )
        self.supports_battle_axis_postprocess = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS
            and capabilities is not None and "battle_axis_postprocess_v1" in capabilities
        )
        self.supports_team_axis_graduation_benchmark = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS and capabilities is not None
            and "team_axis_graduation_benchmark_v2" in capabilities
        )
        self.supports_team_axis_graduation_current = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS and capabilities is not None
            and "team_axis_graduation_current_v2" in capabilities
        )
        self.supports_team_axis_graduation_advice = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS and capabilities is not None
            and "team_axis_graduation_cultivation_advice_v2" in capabilities
        )
        self.supports_team_axis_graduation_equipment_advice = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS and capabilities is not None
            and "team_axis_graduation_equipment_advice_v2" in capabilities
        )
        self.supports_team_axis_graduation_fixed_axis_ratio = (
            engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS and capabilities is not None
            and "team_axis_graduation_fixed_axis_ratio_v1" in capabilities
        )
        self.supports_native_payload_fragments = (
            capabilities is not None and "battle_native_payload_fragments_v1" in capabilities
        )
        self.timeout = timeout
        self.cancelled = cancelled
        if component_identity is None:
            digest = hashlib.sha256()
            try:
                with self.executable.open('rb') as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                        self._checkpoint(None)
            except OSError:
                raise NativeAnalysisError('独立分析候选读取失败') from None
            component_identity = AnalysisComponentIdentity(self.executable, None, digest.hexdigest(), '')
        self._component_identity = component_identity
        self._lock = threading.Lock()
        self._init_worker_session()
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
            # 启动/管道等待独立统计，便于确认短进程开销而不把它误算成 Rust 搜索。
            "process_launches": 0, "process_launch_seconds": 0.0,
            "process_wait_seconds": 0.0,
            "worker_requests": 0, "worker_restarts": 0,
        }
        self._timing_samples: list[dict[str, Any]] = []

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
    def component_provenance(self) -> dict[str, str | None]:
        """Return the already bound source, never re-label a historic preview from today's package."""
        return {"component_executable_sha256": self._component_identity.executable_sha256,
                "component_manifest_sha256": self._component_identity.manifest_sha256 or None}

    @property
    def stats(self) -> dict[str, int | float]:
        with self._lock:
            return dict(self._stats)

    @property
    def timing_samples(self) -> tuple[dict[str, Any], ...]:
        """返回最近的阶段计时样本；样本只含公开耗时与数量，不含请求正文。"""
        with self._lock:
            return tuple(dict(row) for row in self._timing_samples)

    @property
    def supports_analysis_worker(self) -> bool:
        return self.engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS and bool(
            getattr(self, "_capabilities", frozenset())
            and "analysis_worker_v1" in self._capabilities
        )

    @property
    def supports_direct_damage(self) -> bool:
        return self.engine_version in CURRENT_PROTOCOL_ENGINE_VERSIONS and "direct_damage_v1" in self._capabilities

    def _record_timing(self, operation: str, *, started: float, jobs: int,
                       input_bytes: int, output_bytes: int,
                       decode_seconds: float, validate_seconds: float,
                       kernel_seconds: float) -> None:
        safe_operation = operation if (
            isinstance(operation, str) and 0 < len(operation) <= 80
            and all(char in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for char in operation)
        ) else "operation_invalid"
        sample = {
            "operation": safe_operation,
            "jobs": jobs,
            "total_seconds": max(0.0, time.perf_counter() - started),
            "decode_seconds": max(0.0, decode_seconds),
            "validate_seconds": max(0.0, validate_seconds),
            "kernel_seconds": max(0.0, kernel_seconds),
            "input_bytes": input_bytes,
            "output_bytes": output_bytes,
        }
        with self._lock:
            self._timing_samples.append(sample)
            del self._timing_samples[:-64]

    def _checkpoint(self, checkpoint: Callable[[], None] | None) -> None:
        if self.cancelled is not None and self.cancelled():
            raise NativeAnalysisCancelled("分析请求已取消或账号上下文已失效")
        if checkpoint is not None:
            checkpoint()

    def _verify_component(self, checkpoint: Callable[[], None] | None) -> None:
        # 正式 factory 始终传入冻结身份；显式诊断构造仍只绑定调用方选定的程序。
        identity = self._component_identity
        if identity is not None:
            if self.executable != identity.executable:
                raise NativeAnalysisError('独立分析组件路径已变更，请重启应用')
            self._verify_leased_component(checkpoint)

    def _run(
        self, payload: bytes | None, *, version: bool = False,
        checkpoint: Callable[[], None] | None = None,
    ) -> bytes:
        self._checkpoint(checkpoint)
        if payload is not None and not version and self._active_worker() is not None:
            return self._active_worker().call(payload, checkpoint)
        self._verify_component(checkpoint)
        args = [str(self.executable)]
        if version:
            args.append("--version-json")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        launch_started = time.perf_counter()
        try:
            process = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=flags,
            )
        except OSError as error:
            raise NativeAnalysisError("独立分析核心无法启动") from error
        launch_seconds = time.perf_counter() - launch_started
        with self._lock:
            self._stats["process_launches"] += 1
            self._stats["process_launch_seconds"] += launch_seconds
        deadline = time.monotonic() + self.timeout
        pending_input = payload
        wait_started = time.perf_counter()
        wait_recorded = False
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
            with self._lock:
                self._stats["process_wait_seconds"] += time.perf_counter() - wait_started
            wait_recorded = True
            return stdout
        finally:
            if not wait_recorded:
                with self._lock:
                    self._stats["process_wait_seconds"] += time.perf_counter() - wait_started
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
                if (rejected.get("engine_version") == self.engine_version
                        and rejected.get("schema_version") == RESPONSE_SCHEMA and isinstance(error, dict)):
                    # 只解释身份已匹配的已知错误码，不拼接组件原始错误文本或请求路径。
                    known_messages = {
                        "damage_job_blueprints_incomplete": "图纸生成已达预算，尚未完成全部候选；请减少参与角色或调整套装后重试，现有方案保持不变。",
                        "damage_job_time_budget": "分配准备已达时间预算；请减少参与角色后重试，现有方案保持不变。",
                        "battle_static_schema_unsupported": "独立分析组件尚未支持当前静态资料结构；请更新与该资料包匹配的分析组件后重试，原始战报保持不变。",
                    }
                    known_code = error.get("code")
                    if isinstance(known_code, str) and known_code in known_messages:
                        raise NativeAnalysisError(known_messages[known_code])
            # Do not surface raw stderr, request JSON or private values.
            raise NativeAnalysisError(
                f"独立分析核心拒绝请求或执行失败（退出码 {returncode}）"
            )  # ui-text: internal

    def _run_progress(
        self, payload: bytes, *, checkpoint: Callable[[], None] | None,
        progress_callback: Callable[[Mapping[str, Any]], None],
    ) -> bytes:
        from src.integrations.analysis_core.native_analysis_stream import (
            NativeStreamError, communicate_progress,
        )
        self._checkpoint(checkpoint)
        self._verify_component(checkpoint)
        launch_started = time.perf_counter()
        try:
            process = subprocess.Popen(
                [str(self.executable), "--progress"], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            raise NativeAnalysisError("独立分析核心无法启动") from error
        with self._lock:
            self._stats["process_launches"] += 1
            self._stats["process_launch_seconds"] += time.perf_counter() - launch_started
        wait_started = time.perf_counter()
        try:
            stdout, returncode = communicate_progress(
                process, payload, timeout=self.timeout, max_output_bytes=MAX_BYTES,
                total_timeout=max(self.timeout, 600.0),
                checkpoint=lambda: self._checkpoint(checkpoint),
                progress_callback=progress_callback,
            )
        except NativeStreamError as error:
            raise NativeAnalysisError(str(error)) from None
        finally:
            with self._lock:
                self._stats["process_wait_seconds"] += time.perf_counter() - wait_started
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
        if self.supports_analysis_worker and "analysis_worker_v1" not in value.get("capabilities", []):
            raise NativeAnalysisError("独立分析核心缺少持久批次工作进程能力")
        if self.supports_direct_damage and "direct_damage_v1" not in value.get("capabilities", []):
            raise NativeAnalysisError("独立分析核心缺少直伤批量能力")
        if self.supports_battle_progress and "battle_progress_v1" not in value.get("capabilities", []):
            raise NativeAnalysisError("独立分析核心缺少战报进度能力")
        if self.supports_battle_page_identity and "battle_page_identity_v1" not in value.get("capabilities", []):
            raise NativeAnalysisError("独立分析核心缺少战报输入身份能力")
        if self.supports_allocation_postprocess and "allocation_postprocess_v1" not in value.get("capabilities", []):
            raise NativeAnalysisError("独立分析核心缺少配装后处理能力")
        if self.supports_battle_axis_evaluation and "battle_axis_evaluate_v1" not in value.get("capabilities", []):
            raise NativeAnalysisError("独立分析核心缺少战报轴批量评价能力")
        if self.supports_battle_axis_postprocess and "battle_axis_postprocess_v1" not in value.get("capabilities", []):
            raise NativeAnalysisError("独立分析核心缺少战报轴装备优化能力")
        for enabled, capability in ((self.supports_team_axis_graduation_benchmark, "team_axis_graduation_benchmark_v2"),
                                    (self.supports_team_axis_graduation_current, "team_axis_graduation_current_v2"),
                                    (self.supports_team_axis_graduation_advice, "team_axis_graduation_cultivation_advice_v2"),
                                    (self.supports_team_axis_graduation_equipment_advice, "team_axis_graduation_equipment_advice_v2"),
                                    (self.supports_team_axis_graduation_fixed_axis_ratio, "team_axis_graduation_fixed_axis_ratio_v1"),
                                    (self.supports_battle_axis_team, "battle_axis_team_v1"),
                                    (self.supports_battle_axis_fixed_team, "battle_axis_fixed_team_v1"),
                                    (self.supports_blueprints, "allocation_blueprints_v1"),
                                    (self.supports_equipment_scoring, "equipment_scoring_v1"),
                                    (self.supports_damage_allocation_job, "damage_allocation_v1"),
                                    (self.supports_allocation_suit_crit, "allocation_suit_crit_v1"),
                                    (self.supports_damage_baseline_fallback, "damage_allocation_baseline_fallback_v1"),
                                    (self.supports_allocation_group_postprocess, "allocation_group_postprocess_v1"),
                                    (self.supports_allocation_group_projection, "allocation_group_postprocess_v2"),
                                    (self.supports_battle_axis_group_postprocess, "battle_axis_group_postprocess_v1"),
                                    (self.supports_battle_axis_group_rematch, "battle_axis_group_postprocess_v2"),
                                    (self.supports_battle_axis_joint_teams, "battle_axis_group_postprocess_v3")):
            if enabled and capability not in value.get("capabilities", []):
                raise NativeAnalysisError("独立分析核心缺少冻结多组装备优化能力")
        return value

    def score_equipment(self, request: Mapping[str, Any], *, checkpoint=None) -> dict[str, Any]:
        from src.integrations.scoring.equipment_scoring import score_equipment
        return score_equipment(self, request, checkpoint=checkpoint)

    def generate_blueprints(self, request: Mapping[str, Any], *, checkpoint=None) -> dict[str, Any]:
        """一次派发冻结形状/棋盘，由 Rust 返回图纸与明确的搜索覆盖。"""
        from src.integrations.allocation.native_blueprints import generate_blueprints
        return generate_blueprints(self, request, checkpoint=checkpoint)

    def postprocess_allocation(self, request: Mapping[str, Any], *, checkpoint=None) -> dict[str, Any]:
        """Keep postprocess transport separate from the established allocation route."""
        from src.integrations.allocation.allocation_postprocess import postprocess_allocation
        return postprocess_allocation(self, request, checkpoint=checkpoint)

    def resolve_battle_axis_team(self, selector, selected_character_ids, *, checkpoint=None):
        from src.integrations.battle_analysis.axis_team import resolve_axis_team
        return resolve_axis_team(self, selector, selected_character_ids, checkpoint=checkpoint)

    def resolve_battle_axis_fixed_team(self, selector, selected_character_ids, anchor_mode, inventory_identities, *, checkpoint=None):
        from src.integrations.battle_analysis.axis_fixed_team import resolve_fixed_team
        return resolve_fixed_team(self, selector, selected_character_ids, anchor_mode, inventory_identities, checkpoint=checkpoint)

    def evaluate_team_axis_graduation(self, request, *, checkpoint=None):
        from src.integrations.battle_analysis.graduation.team_axis_graduation import evaluate_team_axis_graduation
        return evaluate_team_axis_graduation(self, request, checkpoint=checkpoint)

    def evaluate_battle_axis(self, request: Mapping[str, Any], *, checkpoint=None) -> dict[str, Any]:
        """Reuse the bounded child transport; do not load a page per equipment candidate."""
        from src.integrations.battle_analysis.battle_axis_evaluation import evaluate_battle_axis
        return evaluate_battle_axis(self, request, checkpoint=checkpoint)

    def postprocess_battle_axis(self, request: Mapping[str, Any], *, checkpoint=None) -> dict[str, Any]:
        from src.integrations.allocation.axis_allocation_postprocess import postprocess_battle_axis
        return postprocess_battle_axis(self, request, checkpoint=checkpoint)

    def postprocess_allocation_groups(self, request: Mapping[str, Any], *, checkpoint=None) -> dict[str, Any]:
        from src.integrations.allocation.allocation_group_postprocess import postprocess_allocation_groups
        return postprocess_allocation_groups(self, request, checkpoint=checkpoint)

    def postprocess_battle_axis_groups(self, request: Mapping[str, Any], *, checkpoint=None) -> dict[str, Any]:
        from src.integrations.allocation.allocation_group_postprocess import postprocess_allocation_groups
        return postprocess_allocation_groups(self, request, axis=True, checkpoint=checkpoint)

    def allocate(
        self, payload: Mapping[str, Any], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Execute one frozen allocation without reading account files."""
        self._checkpoint(checkpoint)
        if not self.supports_allocation:
            raise NativeAnalysisError("分析组件缺少空幕分配能力，请更新分析组件")
        if payload.get("batch_kind") != "allocation_v1":
            raise NativeAnalysisError("空幕分配请求类型无效")
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise NativeAnalysisError("空幕分配输入超过大小限制")
        response = _json_object(self._run(encoded, checkpoint=checkpoint))
        if (response.get("batch_kind") != "allocation_v1"
                or type(response.get("version")) is not int or response["version"] != 2
                or not isinstance(response.get("plans"), dict)):
            raise NativeAnalysisError("空幕分配响应协议不匹配")
        self._checkpoint(checkpoint)
        return response["plans"]

    def compute_batch(
        self, operation: str, inputs: Sequence[dict[str, Any]], *,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        from src.integrations.battle_analysis.native_battle_compute import compute_battle_batch
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
        request = dict(request)
        # 编辑事实没有逐击载荷；附加分片字典会破坏其严格的独立响应契约。
        if self.supports_native_payload_fragments and request.get("detail_level") not in {"identity", "editor"}:
            request["native_payload_encoding"] = "fragments_v1"
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
        decode_started = time.perf_counter()
        response = _json_object(raw)
        decode_seconds = time.perf_counter() - decode_started
        validate_started = time.perf_counter()
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
        validate_seconds = time.perf_counter() - validate_started
        with self._lock:
            self._stats["page_calls"] += 1
            self._stats["page_seconds"] += time.perf_counter() - started
            self._stats["page_kernel_seconds"] += kernel_ns / 1_000_000_000
            self._stats["page_input_bytes"] += len(payload)
            self._stats["page_output_bytes"] += len(raw)
        self._record_timing("battle_page", started=started, jobs=1,
                            input_bytes=len(payload), output_bytes=len(raw),
                            decode_seconds=decode_seconds,
                            validate_seconds=validate_seconds,
                            kernel_seconds=kernel_ns / 1_000_000_000)
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
        decode_started = time.perf_counter()
        response = _json_object(raw)
        decode_seconds = time.perf_counter() - decode_started
        validate_started = time.perf_counter()
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
        self._record_timing("evaluate", started=started, jobs=len(jobs),
                            input_bytes=len(payload), output_bytes=len(raw),
                            decode_seconds=decode_seconds,
                            validate_seconds=time.perf_counter() - validate_started,
                            kernel_seconds=kernel_ns / 1_000_000_000)
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

    def calculate_direct_batch(self, inputs, *, checkpoint=None):
        from src.integrations.analysis_core.direct_damage import calculate_direct_batch
        return calculate_direct_batch(self, inputs, checkpoint=checkpoint)
