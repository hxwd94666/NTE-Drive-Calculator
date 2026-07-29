# 定义跨控制器、工作线程和服务传递的不可变日志上下文。
"""Immutable correlation context for one user-visible operation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Correlation fields explicitly passed across thread boundaries."""

    feature: str
    operation_id: str
    account_id: str | None = None
    context_generation: int | None = None
    snapshot_id: int | None = None
    job_id: int | str | None = None

    @classmethod
    def create(
        cls,
        feature: str,
        *,
        account_id: str | None = None,
        context_generation: int | None = None,
        snapshot_id: int | None = None,
        job_id: int | str | None = None,
    ) -> "OperationContext":
        normalized_feature = str(feature).strip()
        if not normalized_feature:
            raise ValueError("日志 feature 不能为空")
        return cls(
            feature=normalized_feature,
            operation_id=uuid4().hex,
            account_id=account_id,
            context_generation=context_generation,
            snapshot_id=snapshot_id,
            job_id=job_id,
        )

    def with_values(
        self,
        *,
        snapshot_id: int | None = None,
        job_id: int | str | None = None,
    ) -> "OperationContext":
        """Return a derived context while preserving the correlation ID."""

        return replace(
            self,
            snapshot_id=self.snapshot_id if snapshot_id is None else snapshot_id,
            job_id=self.job_id if job_id is None else job_id,
        )

    def as_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {
            "feature": self.feature,
            "operation_id": self.operation_id,
        }
        optional = {
            "account_id": self.account_id,
            "context_generation": self.context_generation,
            "snapshot_id": self.snapshot_id,
            "job_id": self.job_id,
        }
        fields.update({key: value for key, value in optional.items() if value is not None})
        return fields
