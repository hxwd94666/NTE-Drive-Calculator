# 按特殊公式分组收集数值作业，批量完成后再恢复原有证据渲染。
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.domain.native_analysis import BattleComputeBackend
from src.services.battle_report.replay.special_numeric import PreparedSpecialReplay


@dataclass(frozen=True, slots=True)
class PendingSpecialReplay:
    index: int


class SpecialReplayBatch:
    def __init__(self, backend):
        self.backend = (
            backend if isinstance(backend, BattleComputeBackend)
            and backend.supports_battle_compute else None
        )
        self._jobs: list[PreparedSpecialReplay] = []

    def submit(self, replay: Callable[..., Any], **kwargs):
        if self.backend is None:
            return replay(**kwargs)

        result = replay(**kwargs, prepare_numeric=True)
        if not isinstance(result, PreparedSpecialReplay):
            return result
        index = len(self._jobs)
        self._jobs.append(result)
        return PendingSpecialReplay(index)

    def resolve(self, *, checkpoint: Callable[[], None]) -> tuple:
        if not self._jobs:
            return ()
        assert self.backend is not None
        groups: dict[str, list[tuple[int, dict]]] = {}
        for index, prepared in enumerate(self._jobs):
            groups.setdefault(prepared.operation, []).append((index, prepared.inputs))
        numeric_results = {}
        for operation, jobs in groups.items():
            checkpoint()
            rows = self.backend.compute_batch(
                operation, tuple(inputs for _, inputs in jobs), checkpoint=checkpoint,
            )
            if len(rows) != len(jobs):
                raise ValueError("Native special formula result count mismatch")
            numeric_results.update((index, row) for (index, _), row in zip(jobs, rows, strict=True))
        results = []
        for index, prepared in enumerate(self._jobs):
            if index % 64 == 0:
                checkpoint()
            results.append(prepared.render(numeric_results[index]))
        return tuple(results)
