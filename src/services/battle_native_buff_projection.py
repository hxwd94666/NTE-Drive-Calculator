# 将冻结逐击与区间序列化为原生批次并恢复完整投影证据。
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import fields, is_dataclass
from typing import Any

from src.domain.native_analysis import BuffProjectionBackend, BuffProjectionBatchTooLarge, BuffProjectionPlanBackend
from src.domain.battle_report import (
    BattleAnalysisHit, BattleInferredBuffInterval, BattleHitBuffProjection,
    BattleProjectedBuffModifier, BattleBuffProjectionDecision,
)


def _wire(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _wire(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    return value


def projection_from_wire(
    row: dict[str, Any], modifiers: dict[int, BattleProjectedBuffModifier] | None = None,
    decisions: dict[int, BattleBuffProjectionDecision] | None = None,
) -> BattleHitBuffProjection:
    modifiers = {} if modifiers is None else modifiers
    decisions = {} if decisions is None else decisions
    for value in row["modifiers"]:
        if id(value) not in modifiers:
            modifiers[id(value)] = BattleProjectedBuffModifier(
                property_id=value["property_id"], additive_value=value["additive_value"],
                interval_ids=tuple(value["interval_ids"]), buff_names=tuple(value["buff_names"]),
                confidence=value["confidence"], target_scope=value["target_scope"],
            )
    for value in row["decisions"]:
        if id(value) not in decisions:
            decisions[id(value)] = BattleBuffProjectionDecision(
                interval_id=value["interval_id"], buff_name=value["buff_name"],
                status=value["status"], applied_property_ids=tuple(value["applied_property_ids"]),
                reasons=tuple(value["reasons"]),
                observed_stacks=value.get("observed_stacks"),
                state_confidence=value.get("state_confidence", ""),
            )
    return BattleHitBuffProjection(
        event_id=row["event_id"],
        modifiers=tuple(modifiers[id(value)] for value in row["modifiers"]),
        applied_interval_ids=tuple(row["applied_interval_ids"]),
        excluded_interval_ids=tuple(row["excluded_interval_ids"]),
        exclusion_reasons=tuple(row["exclusion_reasons"]), confidence=row["confidence"],
        decisions=tuple(decisions[id(value)] for value in row["decisions"]),
    )


ProjectionInput = tuple[
    tuple[int, tuple[int, ...], tuple[int, ...]], BattleAnalysisHit,
    Sequence[BattleInferredBuffInterval], Sequence[BattleInferredBuffInterval]
]

ProjectionPlanInput = tuple[
    tuple[int, int, int], BattleAnalysisHit,
    Sequence[BattleInferredBuffInterval], tuple[int, ...],
]


class NativeBuffProjectionBatch:
    """Request-owned serialization reuse; contains no Buff rules or formula decisions."""

    def __init__(self, backend: BuffProjectionBackend) -> None:
        self.backend = backend
        self.checkpoint: Callable[[], None] | None = None
        self._intervals: dict[int, tuple[BattleInferredBuffInterval, dict[str, Any]]] = {}

    @property
    def supports_plan(self) -> bool:
        return isinstance(self.backend, BuffProjectionPlanBackend) and self.backend.supports_projection_plan

    def project_plan(self, inputs: Sequence[ProjectionPlanInput]) -> tuple[BattleHitBuffProjection, ...]:
        """Send full candidate sets; Rust owns time and recipient selection."""
        backend = self.backend
        if not isinstance(backend, BuffProjectionPlanBackend) or not backend.supports_projection_plan:
            raise ValueError("Backend does not support frozen projection plans")
        payload = self._plan_payload(inputs)
        try:
            rows = backend.project_plan_batch(payload, checkpoint=self.checkpoint)
        except BuffProjectionBatchTooLarge:
            if len(inputs) <= 1:
                raise
            del payload
            midpoint = len(inputs) // 2
            return self.project_plan(inputs[:midpoint]) + self.project_plan(inputs[midpoint:])
        del payload
        modifiers: dict[int, BattleProjectedBuffModifier] = {}
        decisions: dict[int, BattleBuffProjectionDecision] = {}
        return tuple(projection_from_wire(row, modifiers, decisions) for row in rows)

    def _plan_payload(self, inputs: Sequence[ProjectionPlanInput]) -> dict[str, Any]:
        hits: list[dict[str, Any]] = []
        intervals: list[dict[str, Any]] = []
        interval_sets: list[list[int]] = []
        hit_indices: dict[tuple[int, int], int] = {}
        interval_indices: dict[int, int] = {}
        set_indices: dict[int, int] = {}
        jobs = []
        for ordinal, (key, hit, candidate_intervals, tokens) in enumerate(inputs):
            if ordinal % 64 == 0 and self.checkpoint is not None:
                self.checkpoint()
            hit_identity = (key[0], key[1])
            if hit_identity not in hit_indices:
                hit_indices[hit_identity] = len(hits)
                hits.append(_wire(hit))
            set_token = key[2]
            if set_token not in set_indices:
                indices = []
                for index, (interval, token) in enumerate(zip(candidate_intervals, tokens, strict=True)):
                    if index % 256 == 0 and self.checkpoint is not None:
                        self.checkpoint()
                    if token not in interval_indices:
                        interval_indices[token] = len(intervals)
                        remembered = self._intervals.get(token)
                        if remembered is None:
                            remembered = (interval, _wire(interval))
                            self._intervals[token] = remembered
                        intervals.append(remembered[1])
                    indices.append(interval_indices[token])
                set_indices[set_token] = len(interval_sets)
                interval_sets.append(indices)
            jobs.append({"job_id": str(ordinal), "hit_index": hit_indices[hit_identity],
                         "interval_set_index": set_indices[set_token]})
        return {"hits": hits, "intervals": intervals, "interval_sets": interval_sets, "jobs": jobs}

    def project(self, inputs: Sequence[ProjectionInput]) -> tuple[BattleHitBuffProjection, ...]:
        hits: list[dict[str, Any]] = []
        intervals: list[dict[str, Any]] = []
        hit_indices: dict[tuple[int, int], int] = {}
        interval_indices: dict[int, int] = {}
        interval_sets: list[list[int]] = []
        set_indices: dict[tuple[int, ...], int] = {}
        jobs = []

        def set_index(values: Sequence[BattleInferredBuffInterval], tokens: tuple[int, ...]) -> int:
            remembered_index = set_indices.get(tokens)
            if remembered_index is not None:
                return remembered_index
            result = []
            for value, identity in zip(values, tokens, strict=True):
                if identity not in interval_indices:
                    interval_indices[identity] = len(intervals)
                    remembered = self._intervals.get(identity)
                    if remembered is None:
                        remembered = (value, _wire(value))
                        self._intervals[identity] = remembered
                    intervals.append(remembered[1])
                result.append(interval_indices[identity])
            index = len(interval_sets)
            set_indices[tokens] = index
            interval_sets.append(result)
            return index

        for ordinal, (key, hit, temporal, active) in enumerate(inputs):
            # Fork adjustment rechecks the temporal boundary in Rust; rule signatures
            # alone intentionally omit time and cannot identify a wire hit.
            identity = (key[0], hit.relative_time_us)
            if identity not in hit_indices:
                hit_indices[identity] = len(hits)
                hits.append(_wire(hit))
            jobs.append({
                "job_id": str(ordinal), "hit_index": hit_indices[identity],
                "temporal_set_index": set_index(temporal, key[1]),
                "active_set_index": set_index(active, key[2]),
            })
        try:
            rows = self.backend.project_batch(
                {"hits": hits, "intervals": intervals, "interval_sets": interval_sets, "jobs": jobs},
                checkpoint=self.checkpoint,
            )
        except BuffProjectionBatchTooLarge:
            if len(inputs) <= 1:
                raise
            midpoint = len(inputs) // 2
            # Do not retain the rejected full request while constructing both halves.
            del hits, hit_indices, jobs
            intervals, interval_sets, interval_indices, set_indices = [], [], {}, {}
            return self.project(inputs[:midpoint]) + self.project(inputs[midpoint:])
        del hits, hit_indices, jobs
        intervals, interval_sets, interval_indices, set_indices = [], [], {}, {}
        modifiers: dict[int, BattleProjectedBuffModifier] = {}
        decisions: dict[int, BattleBuffProjectionDecision] = {}
        return tuple(projection_from_wire(row, modifiers, decisions) for row in rows)
