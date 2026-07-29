# 定义 Windows 验证报告、检查结果和人工步骤的稳定数据模型。
"""Serializable models used by the maintenance validator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ValidationStatus = Literal["passed", "failed", "skipped", "warning"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    key: str
    status: ValidationStatus
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StepResult:
    profile: str
    title: str
    status: ValidationStatus
    note: str = ""
    checks: tuple[CheckResult, ...] = ()


@dataclass(slots=True)
class ValidationReport:
    session_id: str
    started_at: str
    target: str
    environment: dict[str, Any]
    hashes_before: dict[str, Any]
    steps: list[StepResult] = field(default_factory=list)
    hashes_after: dict[str, Any] = field(default_factory=dict)
    finished_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

